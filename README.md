# Meta Ad Library MCP

An [MCP](https://modelcontextprotocol.io) server that searches the **Meta (Facebook)
Ad Library** by keyword and returns each ad's **advertiser, full ad copy, and EU
reach** (per‑country / age / gender breakdown) — the data the official Ad Library
API does **not** expose for commercial ads.

It works by driving the *public* Ad Library like a real user (Playwright + your real
Chrome) and reading the page's own data — no API key, no login required.

```
search_ads("dental implants", country="DE", limit=50, fetch_reach=True)
  → 50 advertisers + ad copy + EU reach, ranked
```

## Requirements

- **A residential internet connection.** Meta `rd_challenge`‑403s datacenter IPs
  (AWS/GCP/VPS) within seconds. On a normal home/office machine it just works; on a
  server you must set `RESIDENTIAL_PROXY_URL` (see [Hosting](#hosting--remote-claudeai)).
- **Python 3.10+**
- **Google Chrome** installed (the server launches your real Chrome for a clean
  fingerprint). No Chrome? Set `META_ADS_CHANNEL=""` to use Playwright's bundled Chromium.

## Install ([uv](https://docs.astral.sh/uv/) — recommended)

```bash
git clone https://github.com/konstantin-tradient/meta-ad-library-mcp.git
cd meta-ad-library-mcp
uv sync                          # creates an isolated venv from pyproject.toml
uv run playwright install chromium
```

Quick local check (no MCP client needed):

```bash
uv run python smoke.py "dental implants" DE     # live: search + reach
uv run --extra dev pytest -q                    # offline parser test
```

<details><summary>Without uv (plain pip)</summary>

```bash
py -m pip install -r requirements.txt && py -m playwright install chromium   # Windows
python3 -m pip install -r requirements.txt && python3 -m playwright install chromium
```
Then run/register with `py -m meta_ads_mcp.server` + `--env PYTHONPATH=<dir>`.
</details>

## Connect it to Claude

The MCP command is just `uv run --directory <clone> meta-ads-mcp` — uv auto-syncs the
venv, so there's nothing to install globally.

### Claude Code (CLI)

```bash
# --scope user = available in every project. Use the clone's ABSOLUTE path.
claude mcp add meta-ads --scope user -- uv run --directory /abs/path/to/meta-ad-library-mcp meta-ads-mcp
```
Restart Claude Code, then ask: *"search the Meta Ad Library for dental implants in Germany."*

### Claude Desktop (Mac/Windows app)

Edit `claude_desktop_config.json`
(Windows: `%APPDATA%\Claude\`, macOS: `~/Library/Application Support/Claude/`) and add
under `mcpServers` (use the **full path to `uv`** — `which uv` / `where uv`):

```jsonc
"meta-ads": {
  "command": "/full/path/to/uv",
  "args": ["run", "--directory", "/abs/path/to/meta-ad-library-mcp", "meta-ads-mcp"]
}
```
Fully **quit and reopen** Claude Desktop (tray → Quit). The tools appear in the chat.

> **claude.ai (web)** can't run a local server — it only connects to *remote* MCP
> servers (public HTTPS URL + auth). See [Hosting](#hosting--remote-claudeai).

## Tools

| Tool | Returns |
|---|---|
| `search_ads(keyword, country="ALL", limit=20, fetch_reach=False)` | `{count, ads:[{library_id, page_name, status, body_text, cta, link_url, start_date, end_date, versions, eu_total_reach?, uk_total_reach?, reach_breakdown?}]}` |
| `get_ad_details(keyword, library_id, country="ALL")` | `{library_id, eu_total_reach, uk_total_reach, gender_audience, age_audience, location_audience, reach_breakdown:[{country, age_gender:[{age_range, male, female, unknown}]}]}` |
| `session_status()` | `{ready, egress_ip, proxy, last_challenge}` |

- Use an **EU country code** (`DE`, `FR`, `NL`…) to surface ads that carry EU‑reach
  data. Ads not delivered in the EU have `eu_total_reach = null` (Meta's design).
- `fetch_reach=True` clicks every result in one warm session (~3–4s/ad) — great for
  ≤50; otherwise list fast and pull reach per‑ad with `get_ad_details`.

## How it works

- **rd_challenge is a soft gate** — the first request 403s but sets a clearance
  cookie; the server warms the session (homepage → library landing) and **retries**,
  which clears it. (This is what made headless work without a paid API.)
- **List = the page's own GraphQL, not DOM scraping.** `search_ads` reads the SSR'd
  first page (embedded JSON) + `AdLibrarySearchPaginationQuery` captured on scroll,
  so advertiser + ad copy are reliable for every ad (incl. image/video), and it
  paginates to 50+.
- **Reach** comes from `AdLibraryV3AdDetailsQuery`, which only fires when you click an
  ad's "See ad details" on the search page — so reach is gathered by clicking, either
  in batch (`fetch_reach=True`) or per‑ad (`get_ad_details`).

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `RESIDENTIAL_PROXY_URL` | – | `http://user:pass@host:port` — route Meta traffic through a residential proxy (required on datacenter hosts) |
| `META_ADS_CHANNEL` | `chrome` | Browser channel; set `""` to use Playwright's bundled Chromium |
| `META_ADS_HEADFUL` | – | `1` to show the browser window (default headless) |
| `META_ADS_PROFILE` | `~/.meta-ads-profile` | Persistent browser profile dir (keeps the warm session) |

## Hosting / remote (claude.ai)

Runs **locally on a residential IP for free** — ideal for interactive use. To reach
it from **claude.ai (web)** or run it **24/7 on a server**, you need:

1. **HTTP transport** — switch `mcp.run()` to `streamable-http` in `server.py`.
2. **A public HTTPS endpoint** with auth (claude.ai connectors expect OAuth).
3. **Residential egress** — set `RESIDENTIAL_PROXY_URL`; there is no free way around
   the datacenter‑IP `rd_challenge`.

## Legal

This automates access to a **public transparency tool** for competitive research. It
nonetheless falls under Meta's automated‑access terms — use a disposable/burner
account if you host it, pace conservatively, and don't hammer. Provided as‑is.
