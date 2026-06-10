"""Drives the public Meta Ad Library like a real user and extracts ad data.

Reliability strategy (proven 2026-06-10): act as a real browser on a real IP.
Search = scrape the rendered result cards; details = open an ad and capture the
`AdLibraryV3AdDetailsQuery` GraphQL response (carries EU reach + breakdown).
ONE persistent context is kept warm across tool calls. Egress IP is whatever
the host has — residential locally; behind a residential proxy on a datacenter
host (else Meta's rd_challenge 403s at navigation).
"""
from __future__ import annotations

import asyncio
import os
import random
from typing import Any

from playwright.async_api import async_playwright

from .parse import parse_ad_details, parse_search_edges

SEARCH_URL = (
    "https://www.facebook.com/ads/library/"
    "?active_status=active&ad_type=all&country={country}"
    "&q={q}&search_type=keyword_unordered&media_type=all"
)
DETAILS_FRIENDLY_NAME = "AdLibraryV3AdDetailsQuery"
SEARCH_FRIENDLY_NAME = "AdLibrarySearchPaginationQuery"

# Pull the SSR'd first page of results out of the embedded JSON script blobs
# (the pagination query only covers page 2+; page 1 is server-rendered).
_SSR_EDGES_JS = r"""
() => {
  const out = [];
  for (const s of document.querySelectorAll('script[type="application/json"]')) {
    if (!s.textContent.includes('search_results_connection')) continue;
    let j; try { j = JSON.parse(s.textContent); } catch (e) { continue; }
    (function walk(o){
      if (o && typeof o === 'object') {
        if (o.search_results_connection && Array.isArray(o.search_results_connection.edges))
          out.push(...o.search_results_connection.edges);
        for (const k in o) walk(o[k]);
      }
    })(j);
  }
  return out;
}
"""

# Find the card containing "Library ID: <id>", climb to its container, and tag
# that card's "See ad details" button so Playwright can click exactly it.
_MARK_DETAIL_BTN_JS = r"""
(libraryId) => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    if (!n.textContent || !n.textContent.includes('Library ID: ' + libraryId)) continue;
    let el = n.parentElement;
    for (let i = 0; i < 12 && el; i++) {
      const btn = [...el.querySelectorAll('div[role="button"], a[role="button"], button')]
        .find(b => /See ad details/i.test(b.innerText || ''));
      if (btn) { btn.setAttribute('data-meta-ads-detail', '1'); return true; }
      el = el.parentElement;
    }
  }
  return false;
}
"""


class AdLibrary:
    def __init__(self, proxy: str | None = None, headless: bool = True):
        self._proxy = proxy or os.environ.get("RESIDENTIAL_PROXY_URL") or None
        self._headless = headless
        # Real Chrome (channel="chrome") has a clean fingerprint Meta trusts;
        # bundled Chromium gets rd_challenge'd. A persistent profile warms the
        # `datr` trust cookie across runs. Both confirmed load-bearing 2026-06-10.
        self._channel = os.environ.get("META_ADS_CHANNEL", "chrome") or None
        self._profile_dir = os.environ.get(
            "META_ADS_PROFILE", os.path.join(os.path.expanduser("~"), ".meta-ads-profile")
        )
        self._pw = None
        self._ctx = None
        self._lock = asyncio.Lock()
        self._last_details: dict[str, Any] | None = None
        self._last_challenge: str | None = None
        self._page_edges: list[dict[str, Any]] = []  # captured AdLibrarySearchPaginationQuery edges

    async def start(self) -> None:
        if self._ctx is not None:
            return  # idempotent: browser launches lazily, only once
        self._pw = await async_playwright().start()
        launch: dict[str, Any] = {
            "headless": self._headless,
            "locale": "en-US",
            "viewport": {"width": 1440, "height": 900},
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        if self._channel:
            launch["channel"] = self._channel
        if self._proxy:
            launch["proxy"] = {"server": self._proxy}
        # persistent context returns the context directly (browser is implicit)
        self._ctx = await self._pw.chromium.launch_persistent_context(
            self._profile_dir, **launch
        )
        # Playwright sets navigator.webdriver=true — Meta's rd_challenge keys on
        # it. A real Chrome returns false; spoof it on every page. (Confirmed
        # load-bearing 2026-06-10: the only diff vs the browser Meta trusts.)
        await self._ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => false});"
        )
        await self._warmup()

    async def _warmup(self) -> None:
        """Acquire Meta's `datr` trust cookie before hitting the Ad Library.
        A cold profile gets rd_challenge'd on first search; warming `datr` via
        the homepage is what a real browser accumulates and Meta trusts."""
        try:
            cookies = await self._ctx.cookies("https://www.facebook.com")
            if any(c["name"] == "datr" for c in cookies):
                return  # already warm (persistent profile)
        except Exception:  # noqa: BLE001
            pass
        page = await self._ctx.new_page()
        try:
            await page.goto("https://www.facebook.com/", timeout=30000,
                            wait_until="domcontentloaded")
            await self._dismiss_consent(page)
            await asyncio.sleep(1.5)
            # Prime the Ad Library session — the first hit rd_challenge-403s but
            # sets the clearance cookie, so subsequent requests pass.
            await page.goto("https://www.facebook.com/ads/library/", timeout=30000,
                            wait_until="domcontentloaded")
            await self._dismiss_consent(page)
            await asyncio.sleep(2.0)
        except Exception:  # noqa: BLE001
            pass
        finally:
            await page.close()

    async def _goto_until(self, page, url, ready_js: str, attempts: int = 5) -> bool:
        """Navigate, retrying through the soft rd_challenge until `ready_js`
        evaluates truthy. The first hit often 403s then clears on retry."""
        for _ in range(attempts):
            try:
                await page.goto(url, timeout=45000, wait_until="domcontentloaded")
                await self._dismiss_consent(page)
                await asyncio.sleep(2.0 + random.uniform(0, 1.0))
                if await page.evaluate(ready_js):
                    return True
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1.5)
        return await page.evaluate(ready_js)

    async def close(self) -> None:
        if self._ctx:
            await self._ctx.close()
        if self._pw:
            await self._pw.stop()

    async def _new_page(self):
        page = await self._ctx.new_page()
        # capture ad-detail GraphQL + detect challenges
        async def on_response(resp):
            try:
                if "/api/graphql" in resp.url:
                    fn = resp.request.headers.get("x-fb-friendly-name", "")
                    if fn == DETAILS_FRIENDLY_NAME and resp.status == 200:
                        self._last_details = await resp.json()
                    elif fn == SEARCH_FRIENDLY_NAME and resp.status == 200:
                        data = await resp.json()
                        edges = (((data.get("data") or {}).get("ad_library_main") or {})
                                 .get("search_results_connection") or {}).get("edges") or []
                        self._page_edges.extend(edges)
                if resp.status == 403 and "rd_challenge" in (resp.url + await _safe_text(resp)):
                    self._last_challenge = resp.url[:120]
            except Exception:  # noqa: BLE001
                pass
        page.on("response", on_response)
        return page

    async def _dismiss_consent(self, page) -> None:
        for label in ("Allow all cookies", "Decline optional cookies", "Only allow essential cookies"):
            try:
                btn = page.get_by_role("button", name=label)
                if await btn.count():
                    await btn.first.click(timeout=2000)
                    return
            except Exception:  # noqa: BLE001
                pass

    async def search(self, keyword: str, country: str = "ALL", limit: int = 10,
                     fetch_reach: bool = False, min_reach: int = 0,
                     stop_after_below: int = 5) -> dict[str, Any]:
        """Search the Ad Library and return up to `limit` ads with advertiser +
        description, sourced from the page's structured GraphQL (SSR first page +
        AdLibrarySearchPaginationQuery on scroll) — robust for image/video ads.

        With fetch_reach=True, also clicks each result in the SAME warm session to
        capture EU reach (one navigation, N paced clicks).

        Meta exposes NO reach/impressions in the list and sorts only by impressions
        (descending) — which is ~monotonic with reach. So when min_reach>0 we walk
        that order and STOP once `stop_after_below` consecutive ads fall below the
        threshold (the rest, lower in the order, are assumed below too), then return
        only ads at/above min_reach, sorted by reach desc. This is the only viable
        "top-by-reach" strategy without clicking all (often 800+) results."""
        async with self._lock:
            from urllib.parse import quote
            self._page_edges = []
            page = await self._new_page()
            try:
                ok = await self._goto_until(
                    page, SEARCH_URL.format(country=country, q=quote(keyword)),
                    "/Library ID:/.test(document.body.innerText)",
                )
                if not ok:
                    return {"error": "rd_challenge", "where": "navigation",
                            "hint": "challenge did not clear after retries"}
                # page 1 is server-rendered — read it from the embedded JSON
                ssr_edges = await page.evaluate(_SSR_EDGES_JS)
                seen: set[str] = set()
                ads: list[dict[str, Any]] = []

                def absorb(edges):
                    for ad in parse_search_edges(edges):
                        lid = ad.get("library_id")
                        if lid and lid not in seen:
                            seen.add(lid)
                            ads.append(ad)

                absorb(ssr_edges)
                # scroll to trigger AdLibrarySearchPaginationQuery until we have `limit`
                max_scrolls = max(20, limit // 8 + 5)
                for _ in range(max_scrolls):
                    if len(ads) >= limit:
                        break
                    captured = self._page_edges
                    self._page_edges = []
                    absorb(captured)
                    if len(ads) >= limit:
                        break
                    await page.mouse.wheel(0, 6000)
                    await asyncio.sleep(1.4 + random.uniform(0, 0.8))
                absorb(self._page_edges)  # final drain
                ads = ads[:limit]

                reach_meta: dict[str, Any] = {}
                if fetch_reach:
                    reach_meta = await self._enrich_reach(
                        page, ads, min_reach=min_reach, stop_after_below=stop_after_below)
                    # keep only ads with a measurable reach; apply min_reach filter
                    ads = [a for a in ads if a.get("eu_total_reach") is not None]
                    if min_reach > 0:
                        ads = [a for a in ads if (a.get("eu_total_reach") or 0) >= min_reach]
                    ads.sort(key=lambda a: a.get("eu_total_reach") or 0, reverse=True)

                out = {"keyword": keyword, "country": country, "count": len(ads), "ads": ads}
                if fetch_reach:
                    out["reach_meta"] = reach_meta
                return out
            finally:
                await page.close()

    async def _enrich_reach(self, page, ads: list[dict[str, Any]], min_reach: int = 0,
                            stop_after_below: int = 5) -> dict[str, Any]:
        """Click each ad (in Meta's impressions order) and merge its EU reach.
        When min_reach>0, stop after `stop_after_below` consecutive ads below the
        threshold — the order is ~reach-descending, so the rest are assumed below.
        Returns {checked, stopped_early, ...} so the caller can report the heuristic."""
        checked = 0
        consecutive_below = 0
        stopped_early = False
        for ad in ads:
            lid = ad.get("library_id")
            if not lid:
                continue
            self._last_details = None
            try:
                marked = await page.evaluate(_MARK_DETAIL_BTN_JS, lid)
                if not marked:
                    continue  # card virtualised out of DOM — skip, don't count
                await page.click('[data-meta-ads-detail="1"]', timeout=8000, force=True)
            except Exception:  # noqa: BLE001
                continue
            for _ in range(25):
                if self._last_details is not None:
                    break
                await asyncio.sleep(0.3)
            got_detail = self._last_details is not None
            if got_detail:
                r = parse_ad_details(self._last_details)
                ad["eu_total_reach"] = r["eu_total_reach"]
                ad["uk_total_reach"] = r["uk_total_reach"]
                ad["reach_breakdown"] = r["reach_breakdown"]
                checked += 1
            try:
                await page.keyboard.press("Escape")
                await page.evaluate(
                    "() => document.querySelectorAll('[data-meta-ads-detail]')"
                    ".forEach(e => e.removeAttribute('data-meta-ads-detail'))"
                )
                await asyncio.sleep(0.7 + random.uniform(0, 0.6))
            except Exception:  # noqa: BLE001
                pass
            # early-stop streak (only meaningful with a threshold + a real detail)
            if min_reach > 0 and got_detail:
                if (ad.get("eu_total_reach") or 0) < min_reach:
                    consecutive_below += 1
                    if consecutive_below >= stop_after_below:
                        stopped_early = True
                        break
                else:
                    consecutive_below = 0
        return {"checked": checked, "stopped_early": stopped_early,
                "min_reach": min_reach, "stop_after_below": stop_after_below}

    async def get_ad_details(self, keyword: str, library_id: str,
                             country: str = "ALL") -> dict[str, Any]:
        """Fetch one ad's EU/UK reach + demographic breakdown. Reach only loads
        when you click the ad on the keyword-search page, so we re-run the search,
        scroll to the card, click it, and capture AdLibraryV3AdDetailsQuery."""
        async with self._lock:
            from urllib.parse import quote
            self._last_details = None
            page = await self._new_page()
            try:
                ok = await self._goto_until(
                    page, SEARCH_URL.format(country=country, q=quote(keyword)),
                    "/Library ID:/.test(document.body.innerText)",
                )
                if not ok:
                    return {"error": "rd_challenge", "where": "navigation"}
                # scroll until the target card is in the DOM, then mark + click it
                marked = False
                for _ in range(20):
                    marked = await page.evaluate(_MARK_DETAIL_BTN_JS, library_id)
                    if marked:
                        break
                    await page.mouse.wheel(0, 6000)
                    await asyncio.sleep(1.2 + random.uniform(0, 0.6))
                if not marked:
                    return {"error": "ad_not_found", "library_id": library_id,
                            "hint": "card not found in first ~20 pages of this keyword"}
                try:
                    await page.click('[data-meta-ads-detail="1"]', timeout=8000, force=True)
                except Exception:  # noqa: BLE001
                    pass
                for _ in range(30):
                    if self._last_details is not None:
                        break
                    await asyncio.sleep(0.3)
                if self._last_details is None:
                    return {"error": "no_detail_payload", "library_id": library_id}
                parsed = parse_ad_details(self._last_details)
                parsed["library_id"] = library_id
                return parsed
            finally:
                await page.close()

    async def status(self) -> dict[str, Any]:
        page = await self._new_page()
        try:
            ip = "unknown"
            try:
                r = await page.goto("https://api.ipify.org?format=json", timeout=15000)
                ip = (await r.json()).get("ip", "unknown")
            except Exception:  # noqa: BLE001
                pass
            return {
                "ready": self._ctx is not None,
                "egress_ip": ip,
                "proxy": bool(self._proxy),
                "last_challenge": self._last_challenge,
            }
        finally:
            await page.close()


async def _safe_text(resp) -> str:
    try:
        return (await resp.text())[:500]
    except Exception:  # noqa: BLE001
        return ""
