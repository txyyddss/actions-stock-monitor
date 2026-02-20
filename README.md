# Actions Stock Monitor

A stock/restock monitor that scrapes a set of provider storefronts, sends Telegram alerts for **new products** and **restocks**, and generates a fast static dashboard (`docs/index.html`).

This repo is designed to run on a schedule via GitHub Actions (see `.github/workflows/main.yml`) and commit updates to:

- `data/state.json` (persistent product state + domain health)
- `docs/index.html` (static dashboard)

## How It Works

### 1) Targets

Targets are base URLs (one per provider) defined in `src/actions_stock_monitor/targets.py`. You can override them at runtime with `--targets`.

### 2) Fetching (Direct First → FlareSolverr Fallback)

`src/actions_stock_monitor/http_client.py` implements:

- **Direct fetch first** using `requests`
- **Cloudflare challenge detection**
- **Fallback to FlareSolverr** (if `FLARESOLVERR_URL` is set)
- **English-biased fetch headers** (`Accept-Language: en-US,en;q=0.9`) for both direct and FlareSolverr requests
- **Temporary cookie + UA reuse** per domain, extracted from FlareSolverr solutions, to reduce repeated challenge solving across multiple pages in the same run
- **Fast secondary crawl behavior**: discovery/detail sub-pages avoid FlareSolverr fallback unless they are primary listing pages

### 3) Parsing

Parsers live in `src/actions_stock_monitor/parsers/`:

- `GenericDomainParser`: HTML card-based parsing for common hosting storefronts
- `SpaStoreApiParser`: JSON-backed SPA storefronts (API endpoints)

The monitor also runs a **discovery pass** for storefronts that only show partial inventories on the landing page (e.g., category teasers). Discovery follows in-domain store/group links and de-dupes products by a stable URL-based id.

### 4) State + Notifications

`data/state.json` stores:

- known products (including last seen / last change)
- per-domain status (ok/error + timing)

Telegram notifications are sent for:

- **NEW PRODUCT**: first time a product is seen *and* in stock
- **RESTOCK ALERT**: transitions from out-of-stock → in-stock

### 5) Dashboard

`src/actions_stock_monitor/dashboard.py` renders a single-page static HTML dashboard with search, sorting, and a per-domain filter.

## Configuration

### GitHub Secrets (recommended)

Add these repository secrets:

- `TELEGRAM_BOT_TOKEN` (required to send Telegram alerts)
- `TELEGRAM_CHAT_ID` (required to send Telegram alerts)
- `PROXY_URL` (optional; `socks5://...` or `http(s)://...`)
- `FLARESOLVERR_URL` (optional; defaults to `http://127.0.0.1:8191` in the workflow)

### Environment Variables

Common settings:

- `TIMEOUT_SECONDS` (default: `25`)
- `MAX_WORKERS` (default: `8`)
- `MONITOR_LOG` (default: `1`; set to `0` to silence per-domain logs)

Discovery tuning:

- `DISCOVERY_MAX_PAGES_PER_DOMAIN` (default: `16`; bumped to `24` for WHMCS, `40` for GreenCloud unless overridden)
- `DISCOVERY_MAX_PRODUCTS_PER_DOMAIN` (default: `500`)
- `DISCOVERY_STOP_AFTER_NO_NEW_PAGES` (default: `4`; `6` for WHMCS)
- `DISCOVERY_STOP_AFTER_FETCH_ERRORS` (default: `4`)
- `DISCOVERY_FORCE_IF_PRODUCTS_LEQ` (default: `6`)
- `DISCOVERY_FORCE_IF_PRIMARY_LISTING_PRODUCTS_LEQ` (default: `40`)

Cloudflare/FlareSolverr:

- `FLARESOLVERR_URL` (e.g. `http://127.0.0.1:8191`)
- `CF_COOKIE_TTL_SECONDS` (default: `1800`)

Telegram rate limiting/backoff:

- `TELEGRAM_MAX_RETRIES`
- `TELEGRAM_RETRY_BASE_SECONDS`
- `TELEGRAM_MIN_INTERVAL_SECONDS`

## Run Locally

Install dependencies:

```powershell
pip install -r requirements.txt
pip install -e .
```

Optional: run FlareSolverr (Docker):

```powershell
docker run --rm -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest
```

Run a single monitor pass (dry run = no Telegram sends):

```powershell
$env:FLARESOLVERR_URL="http://127.0.0.1:8191"
python -m actions_stock_monitor --state data/state.json --output docs/index.html --dry-run
```

## Deploy Dashboard to Cloudflare Pages (Static)

Cloudflare Pages is ideal for hosting the **static dashboard**. It does not run the scheduled scraping job itself; keep the schedule in GitHub Actions.

1) Ensure `docs/index.html` exists in the repo
   - Run locally once, or let GitHub Actions run at least once (workflow commits `docs/index.html`).

2) In Cloudflare Dashboard → **Pages** → **Create a project**
   - Connect your GitHub account and select this repository.

3) Configure build settings
   - **Framework preset**: `None`
   - **Build command**: leave empty (or `echo "no build"`)
   - **Build output directory**: `docs`

4) Deploy
   - Cloudflare Pages will publish the contents of `docs/` and serve `docs/index.html` as the site root.

5) Keep the dashboard fresh
   - GitHub Actions commits updates to `docs/index.html` and Cloudflare Pages will redeploy automatically on each commit.

## GitHub Pages (Alternative)

This repo also supports GitHub Pages: configure Pages to publish from the `main` branch and the `/docs` folder.
