"""Meta Ad Library MCP — search ads by keyword + fetch description & EU reach.

Runs a warm headless Chromium that drives the public Ad Library as a real user.
Reliable on a residential IP (run locally) or behind a residential proxy
(RESIDENTIAL_PROXY_URL) on a datacenter host. stdio transport — add with:

    claude mcp add meta-ads -- <python> -m meta_ads_mcp.server
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from .browser import AdLibrary

_lib: AdLibrary | None = None


@asynccontextmanager
async def _lifespan(_server: FastMCP):
    # Create the controller but do NOT launch Chromium here — keep MCP startup
    # instant so it always registers within the client's init timeout. The
    # browser launches lazily on the first tool call (start() is idempotent).
    global _lib
    _lib = AdLibrary(
        proxy=os.environ.get("RESIDENTIAL_PROXY_URL"),
        headless=os.environ.get("META_ADS_HEADFUL") != "1",
    )
    try:
        yield
    finally:
        await _lib.close()
        _lib = None


mcp = FastMCP("meta-ads", lifespan=_lifespan)


@mcp.tool()
async def search_ads(keyword: str, country: str = "ALL", limit: int = 20,
                     fetch_reach: bool = False, min_reach: int = 0,
                     stop_after_below: int = 5) -> dict:
    """Search the Meta Ad Library by keyword — returns the advertiser +
    description for each ad (supports loading 50+). Fast by default.

    Args:
        keyword: free-text query (e.g. "dental implants").
        country: ISO-2 country code (e.g. "DE", "AE"); use an EU country to find
                 ads with EU-reach data. "ALL" = no country filter.
        limit: max ads to scan (paginates by scrolling). With min_reach this is the
               safety ceiling — set it high (e.g. 300) and let min_reach stop it.
        fetch_reach: if True, clicks each result to add eu_total_reach + breakdown
                     (slower: ~3-4s/ad).
        min_reach: only return ads with EU reach ≥ this. Meta has NO reach sort and
                   hides impressions, but its impressions order is ~reach-descending,
                   so results come back reach-sorted and scanning stops early (see
                   stop_after_below) instead of clicking all 800+ ads. 0 = no filter.
        stop_after_below: with min_reach>0, stop after this many CONSECUTIVE ads
                          below the threshold (absorbs frequency noise; raise it for
                          multi-advertiser keywords where the order is noisier).
    Returns: {keyword, country, count, ads:[{library_id, page_name, status,
              body_text, cta, link_url, start_date, end_date, versions,
              eu_total_reach?, uk_total_reach?, reach_breakdown?}],
              reach_meta?:{checked, stopped_early, min_reach, stop_after_below}}.
              With fetch_reach, ads are sorted by eu_total_reach descending.
    Ads not delivered in the EU have eu_total_reach=null and are dropped when
    fetching reach. For a single ad later: get_ad_details(keyword, library_id, country).
    """
    await _lib.start()  # lazy: launches + warms Chromium on first call
    return await _lib.search(keyword, country=country, limit=limit, fetch_reach=fetch_reach,
                             min_reach=min_reach, stop_after_below=stop_after_below)


@mcp.tool()
async def get_ad_details(keyword: str, library_id: str, country: str = "ALL") -> dict:
    """Fetch one ad's EU/UK reach + per-country/age/gender breakdown.

    Args:
        keyword: the same keyword you searched (reach only loads from the
                 keyword-search page, so we re-find the ad there).
        library_id: the ad's Library ID (from search_ads).
        country: ISO-2 country code, or "ALL".
    Returns: {library_id, eu_total_reach, uk_total_reach, gender_audience,
              age_audience, location_audience, reach_breakdown:[{country, age_gender:[...]}]}.
    Note: ads not delivered in the EU have eu_total_reach=null (Meta's design).
    """
    await _lib.start()
    return await _lib.get_ad_details(keyword, library_id, country=country)


@mcp.tool()
async def session_status() -> dict:
    """Health check: is the browser ready, what egress IP, any recent challenge?"""
    await _lib.start()
    return await _lib.status()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
