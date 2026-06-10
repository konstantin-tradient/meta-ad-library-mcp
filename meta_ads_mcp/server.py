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
                     fetch_reach: bool = False) -> dict:
    """Search the Meta Ad Library by keyword — returns the advertiser +
    description for each ad (supports loading 50+). Fast by default.

    Args:
        keyword: free-text query (e.g. "dental implants").
        country: ISO-2 country code (e.g. "DE", "AE"); use an EU country to find
                 ads with EU-reach data. "ALL" = no country filter.
        limit: max ads to return (paginates by scrolling; 50 is fine).
        fetch_reach: if True, also clicks each result in one warm session to add
                     eu_total_reach + breakdown (slower: ~3-4s/ad; use for ≤50).
    Returns: {keyword, country, count, ads:[{library_id, page_name, status,
              body_text, cta, link_url, start_date, end_date, versions,
              eu_total_reach?, uk_total_reach?, reach_breakdown?}]}.
    Ads not delivered in the EU have eu_total_reach=null (Meta's design).
    For a single ad's reach later, use get_ad_details(keyword, library_id, country).
    """
    await _lib.start()  # lazy: launches + warms Chromium on first call
    return await _lib.search(keyword, country=country, limit=limit, fetch_reach=fetch_reach)


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
