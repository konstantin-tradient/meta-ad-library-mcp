"""Local smoke test: load N ads with advertiser+body, then reach for one."""
import asyncio
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from meta_ads_mcp.browser import AdLibrary


async def main(keyword: str, country: str, limit: int):
    lib = AdLibrary(headless="--headful" not in sys.argv)
    await lib.start()
    try:
        print("STATUS:", json.dumps(await lib.status()))
        res = await lib.search(keyword, country=country, limit=limit)
        print("SEARCH:", json.dumps({k: v for k, v in res.items() if k != "ads"}))
        ads = res.get("ads", [])
        n_body = sum(1 for a in ads if a.get("body_text"))
        n_adv = sum(1 for a in ads if a.get("page_name"))
        print(f"coverage: {n_adv}/{len(ads)} advertiser, {n_body}/{len(ads)} body_text")
        for a in ads[:8]:
            print(f"  - {a.get('library_id')} | {a.get('page_name')} | {a.get('status')} "
                  f"| {(a.get('body_text') or '(none)')[:50]}")
        if ads:
            d = await lib.get_ad_details(keyword, ads[1]["library_id"], country=country)
            print("REACH(ad#2):", json.dumps({k: d.get(k) for k in
                  ("library_id", "eu_total_reach", "uk_total_reach")}),
                  "breakdown_rows:", len(d.get("reach_breakdown") or []))
    finally:
        await lib.close()


if __name__ == "__main__":
    kw = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "dental implants"
    co = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "DE"
    lim = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 50
    asyncio.run(main(kw, co, lim))
