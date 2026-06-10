#!/usr/bin/env python3
"""Phase-0 rate-limit probe for the Meta Ad Library MCP.

NOT the MCP. A throwaway instrument that drives the public Ad Library like a
user and CLASSIFIES what blocks it, so "rate limited" becomes one of four
known, fixable signals instead of a guess. See the plan:
~/.claude/plans/i-would-like-to-gleaming-sunrise.md  (Derisking section).

Run ON the EC2 box (datacenter IP parity), one variable at a time:

  # baseline: datacenter IP, no proxy, no session, headless
  python3 probe.py --keyword "dental implants" --country AE

  # + residential proxy
  python3 probe.py --keyword "dental implants" --country AE \
      --proxy "http://user:pass@host:port"

  # + warm logged-in burner session (persistent profile) + headful (needs xvfb)
  xvfb-run -a python3 probe.py --keyword "dental implants" --country AE \
      --proxy "http://user:pass@host:port" \
      --user-data-dir /srv/meta-ads/profile --headful

Emits a JSON verdict to stdout: egress IP, the signal classification, how many
GraphQL calls succeeded before failure, and the GraphQL friendly_names seen
(those name the real search/detail queries the MCP will capture later).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from urllib.parse import quote

from playwright.async_api import async_playwright

ADLIB_URL = (
    "https://www.facebook.com/ads/library/"
    "?active_status=active&ad_type=all&country={country}"
    "&q={q}&search_type=keyword_unordered&media_type=all"
)
GRAPHQL_MARKER = "/api/graphql"
IP_ECHO = "https://api.ipify.org?format=json"


def classify(signals: list[dict]) -> dict:
    """Reduce captured events to the single dominant blocking signal + fix."""
    for s in signals:
        if s["type"] == "checkpoint":
            return {"signal": "checkpoint", "fix": "account flagged -> stop, re-login via noVNC, burner is disposable"}
    for s in signals:
        if s["type"] == "rd_challenge":
            return {"signal": "rd_challenge", "fix": "datacenter IP / bot fingerprint -> residential proxy + headful + warm session"}
    for s in signals:
        if s["type"] == "error_613":
            return {"signal": "error_613", "fix": "calls too fast -> pacing + jitter + per-query cap + backoff"}
    for s in signals:
        if s["type"] == "login_redirect":
            return {"signal": "login_redirect", "fix": "session expired / IP changed mid-session -> sticky proxy IP + warm session"}
    return {"signal": "none", "fix": "clean run"}


async def get_egress_ip(context) -> str:
    page = await context.new_page()
    try:
        resp = await page.goto(IP_ECHO, timeout=20000)
        data = await resp.json()
        return data.get("ip", "unknown")
    except Exception as e:  # noqa: BLE001
        return f"ip-check-failed: {e}"
    finally:
        await page.close()


async def run(args) -> dict:
    signals: list[dict] = []
    friendly_names: set[str] = set()
    graphql_ok = 0
    graphql_total = 0

    async with async_playwright() as pw:
        launch_kwargs: dict = {"headless": not args.headful}
        if args.proxy:
            launch_kwargs["proxy"] = {"server": args.proxy}

        if args.user_data_dir:
            context = await pw.chromium.launch_persistent_context(
                args.user_data_dir, **launch_kwargs
            )
        else:
            browser = await pw.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
            )

        if args.block_images:
            await context.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "media", "font")
                else route.continue_(),
            )

        egress_ip = await get_egress_ip(context)

        page = await context.new_page()

        async def on_response(resp):
            nonlocal graphql_ok, graphql_total
            url = resp.url
            if GRAPHQL_MARKER not in url:
                if "/checkpoint/" in url:
                    signals.append({"type": "checkpoint", "where": url[:120], "status": resp.status})
                return
            graphql_total += 1
            fn = resp.request.headers.get("x-fb-friendly-name", "")
            if fn:
                friendly_names.add(fn)
            status = resp.status
            try:
                body = await resp.text()
            except Exception:  # noqa: BLE001
                body = ""
            if status == 403 or "rd_challenge" in body[:2000]:
                signals.append({"type": "rd_challenge", "where": fn or url[:80], "status": status})
            elif '"code":613' in body or "exceeded the rate" in body.lower():
                signals.append({"type": "error_613", "where": fn or url[:80], "status": status})
            elif status >= 400:
                signals.append({"type": f"http_{status}", "where": fn or url[:80], "status": status})
            else:
                graphql_ok += 1

        page.on("response", on_response)

        def on_frame_nav(frame):
            u = frame.url
            if "/checkpoint/" in u:
                signals.append({"type": "checkpoint", "where": u[:120], "status": 0})
            elif u.startswith("https://www.facebook.com/login") or "login.php" in u:
                signals.append({"type": "login_redirect", "where": u[:120], "status": 0})

        page.on("framenavigated", on_frame_nav)

        target = ADLIB_URL.format(country=args.country, q=quote(args.keyword))
        nav_status = None
        try:
            resp = await page.goto(target, timeout=45000, wait_until="domcontentloaded")
            nav_status = resp.status if resp else None
            if resp and resp.status == 403:
                signals.append({"type": "rd_challenge", "where": "navigation", "status": 403})
        except Exception as e:  # noqa: BLE001
            signals.append({"type": "nav_error", "where": str(e)[:120], "status": 0})

        # Scroll to trigger pagination queries, paced, up to the limit.
        for i in range(args.limit):
            await page.mouse.wheel(0, 4000)
            await asyncio.sleep(args.delay + random.uniform(0, args.delay))
            if any(s["type"] in ("rd_challenge", "checkpoint", "error_613") for s in signals):
                break

        await context.close()

    verdict = classify(signals)
    return {
        "args": {k: v for k, v in vars(args).items() if k != "proxy"} | {"proxy": bool(args.proxy)},
        "egress_ip": egress_ip,
        "nav_status": nav_status,
        "graphql_total": graphql_total,
        "graphql_ok": graphql_ok,
        "friendly_names": sorted(friendly_names),
        "signals": signals[:20],
        "verdict": verdict,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Meta Ad Library rate-limit probe")
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--country", default="ALL")
    ap.add_argument("--proxy", default=None, help="http://user:pass@host:port")
    ap.add_argument("--user-data-dir", default=None, help="persistent profile (warm session)")
    ap.add_argument("--headful", action="store_true", help="needs xvfb on a headless box")
    ap.add_argument("--limit", type=int, default=8, help="scroll iterations (pagination depth)")
    ap.add_argument("--delay", type=float, default=2.0, help="base seconds between scrolls (jittered)")
    ap.add_argument("--block-images", action="store_true", help="cut proxy bandwidth cost")
    args = ap.parse_args()

    t0 = time.time()
    result = asyncio.run(run(args))
    result["elapsed_s"] = round(time.time() - t0, 1)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
