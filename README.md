# Meta Ad Library MCP

Search the Meta (Facebook) Ad Library by keyword and get each ad's **description +
EU reach** (per-country/age/gender breakdown) — the data the official Ad Library
API does NOT expose for commercial ads.

## How it works

Drives the public Ad Library like a real user (Playwright) and reads the data the
page itself receives. The reliability tricks (all confirmed 2026-06-10):

- **Residential IP required.** Meta `rd_challenge`-403s datacenter IPs (e.g. AWS).
  Run on a residential connection, or behind a residential proxy.
- **rd_challenge is a soft gate** — the first hit 403s but sets a clearance cookie;
  we warm the session (homepage + library landing) and **retry**, which clears it.
- **List = structured GraphQL, not DOM scraping.** `search_ads` reads the ad data
  from the page's own payload — the SSR'd first page (embedded JSON) +
  `AdLibrarySearchPaginationQuery` captured on scroll — so advertiser + body_text
  are reliable for every ad (incl. image/video), and it paginates to 50+.
- **Reach lives in `AdLibraryV3AdDetailsQuery`**, which only fires when you click an
  ad's "See ad details" on the *keyword search* page — so reach is a separate,
  on-demand call (`get_ad_details`) that re-finds the ad and clicks it.

## Tools

- `search_ads(keyword, country="ALL", limit=20)` →
  `{count, ads:[{library_id, page_name, status, body_text, cta, link_url,
  start_date, end_date, versions}]}`. Fast; supports `limit=50+`. Use an **EU
  country** (e.g. `DE`) to find ads that also carry EU-reach data.
- `get_ad_details(keyword, library_id, country="ALL")` →
  `{library_id, eu_total_reach, uk_total_reach, gender_audience, age_audience,
  location_audience, reach_breakdown:[{country, age_gender:[{age_range, male,
  female, unknown}]}]}`. Ads not delivered in the EU have `eu_total_reach=null`.
- `session_status()` → `{ready, egress_ip, proxy, last_challenge}`.

## Run / install

```bash
py -m pip install -r requirements.txt
py -m playwright install chromium
PYTHONPATH=. py smoke.py "dental implants" DE      # local smoke test
PYTHONPATH=. py -m pytest meta_ads_mcp/tests/ -q   # parser test (offline fixture)
```

Add to Claude Code (stdio):

```bash
claude mcp add meta-ads --scope local \
  --env "PYTHONPATH=<abs path to this dir>" -- py -m meta_ads_mcp.server
```

Env: `RESIDENTIAL_PROXY_URL` (http://user:pass@host:port) to route via a proxy;
`META_ADS_HEADFUL=1` to show the browser; `META_ADS_CHANNEL` (default `chrome`);
`META_ADS_PROFILE` (persistent profile dir, default `~/.meta-ads-profile`).

## Hosting note

Runs **locally on a residential IP for free** (interactive use). To host 24/7 on a
datacenter box (e.g. the EC2 automation host) it needs a **residential proxy** via
`RESIDENTIAL_PROXY_URL` — no free way around the datacenter-IP block. See the plan
at `~/.claude/plans/i-would-like-to-gleaming-sunrise.md`.
