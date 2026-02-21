# Actions Stock Monitor

A stock/restock monitor that scrapes a set of provider storefronts, sends Telegram alerts for new products and restocks, and generates a fast static dashboard (`docs/index.html`).

This repo is designed to run on a schedule via GitHub Actions (`.github/workflows/main.yml` for full updates and `.github/workflows/lite.yml` for lite updates) and commit updates to:

- `data/state.json` (persistent product state + domain health)
- `docs/index.html` (static dashboard)

## How It Works

### 1) Targets

Targets are base URLs (one per provider) defined in `src/actions_stock_monitor/targets.py`. You can override them at runtime with `--targets`.

### 2) Fetching (Direct First -> FlareSolverr Fallback)

`src/actions_stock_monitor/http_client.py` implements:

- Direct fetch first using `requests`
- Cloudflare challenge detection
- Fallback to FlareSolverr (if `FLARESOLVERR_URL` is set)
- English-biased fetch headers (`Accept-Language: en-US,en;q=0.9`)
- Temporary cookie reuse per domain (when available)
- Stable per-domain User-Agent during a run to reduce anti-bot churn (FlareSolverr v2 removes the `userAgent` request parameter)

### 3) Parsing

Parsers live in `src/actions_stock_monitor/parsers/`:

- `GenericDomainParser`: HTML card-based parsing for common hosting storefronts
- `SpaStoreApiParser`: JSON-backed SPA storefronts (API endpoints)

The monitor also runs a discovery pass for storefronts that only show partial inventories on the landing page (e.g., category teasers). Discovery follows in-domain store/group links and de-dupes products by a stable URL-based id.

### 4) State + Notifications

`data/state.json` stores:

- known products (including last seen / last change)
- per-domain status (ok/error + timing)

Telegram notifications are sent for:

- NEW PRODUCT: first time a product is seen and in stock
- RESTOCK ALERT: transitions from out-of-stock -> in-stock
- NEW LOCATION: a new in-stock location variant for an existing plan

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
- `MAX_WORKERS` (default: `4`)
- `MONITOR_LOG` (default: `1`; set to `0` to silence per-domain logs)
- `MONITOR_MODE` (`full` or `lite`; default: `full`)
- `TARGET_MAX_DURATION_SECONDS` (default: `210`; per-target soft deadline for expansion/discovery)

Discovery tuning:

- `DISCOVERY_MAX_PAGES_PER_DOMAIN` (default: `16`; bumped for some WHMCS and GreenCloud domains unless overridden)
- `DISCOVERY_WORKERS` (default: `4`)
- `DISCOVERY_BATCH` (default: `6`)
- `DISCOVERY_MAX_PRODUCTS_PER_DOMAIN` (default: `500`)
- `DISCOVERY_STOP_AFTER_NO_NEW_PAGES` (default: `4`; `0` for WHMCS by default)
- `DISCOVERY_STOP_AFTER_FETCH_ERRORS` (default: `4`)
- `DISCOVERY_FORCE_IF_PRODUCTS_LEQ` (default: `6`)
- `DISCOVERY_FORCE_IF_PRIMARY_LISTING_PRODUCTS_LEQ` (default: `40`)

Cloudflare/FlareSolverr:

- `FLARESOLVERR_URL` (e.g. `http://127.0.0.1:8191`)
- `CF_COOKIE_TTL_SECONDS` (default: `1800`)

Hidden WHMCS scanning (pid/gid brute scan):

- `WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO` (default: `10`; falls back to `WHMCS_HIDDEN_STOP_AFTER_MISS` if unset)
- `WHMCS_HIDDEN_GID_STOP_AFTER_SAME_PAGE` (default: `5`)
- `WHMCS_HIDDEN_PID_STOP_AFTER_NO_PROGRESS` (default: `60`)
- `WHMCS_HIDDEN_GID_STOP_AFTER_NO_PROGRESS` (default: `60`)
- `WHMCS_HIDDEN_PID_STOP_AFTER_DUPLICATES` (default: `18`)
- `WHMCS_HIDDEN_GID_STOP_AFTER_DUPLICATES` (default: `18`)
- `WHMCS_HIDDEN_REDIRECT_SIGNATURE_STOP_AFTER` (default: `12`)
- `WHMCS_HIDDEN_MAX_DURATION_SECONDS` (default: `60`; per-domain hidden scan time cap)
- `WHMCS_HIDDEN_STOP_AFTER_MISS` (legacy fallback for pid stop threshold)
- `WHMCS_HIDDEN_MIN_PROBE` (default: `0`)
- `WHMCS_HIDDEN_BATCH` (default: `3`)
- `WHMCS_HIDDEN_WORKERS` (default: `2`)
- `WHMCS_HIDDEN_HARD_MAX_PID` (default: `2000`)
- `WHMCS_HIDDEN_HARD_MAX_GID` (default: `2000`)
- `WHMCS_HIDDEN_PID_CANDIDATES_MAX` (default: `200`)
- `WHMCS_HIDDEN_LOG` (default: `0`; set to `1` to print in-stock hits from hidden scans)

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

Run lite mode (state-driven targets, no full expansion/pruning):

```powershell
python -m actions_stock_monitor --state data/state.json --output docs/index.html --mode lite --dry-run
```

Live smoke test (per-domain) is available via pytest:

```powershell
$env:RUN_LIVE_TESTS="1"
$env:FLARESOLVERR_URL="http://127.0.0.1:8191"
pytest -q
```

Single-site debugging workflow (sequential, one target at a time):

```powershell
python scripts/site_debug.py --target https://acck.io/ --stage simple --flaresolverr-url http://127.0.0.1:8191/
python scripts/site_debug.py --target https://acck.io/ --stage monitor --flaresolverr-url http://127.0.0.1:8191/
```

## Deploy Dashboard to Cloudflare Pages (Static)

Cloudflare Pages is ideal for hosting the static dashboard. It does not run the scheduled scraping job itself; keep the schedule in GitHub Actions.

1) Ensure `docs/index.html` exists in the repo.
2) In Cloudflare Dashboard -> Pages -> Create a project.
3) Configure build settings:
   - Framework preset: None
   - Build command: empty (or `echo "no build"`)
   - Build output directory: `docs`
4) Deploy.

## GitHub Pages (Alternative)

This repo also supports GitHub Pages: configure Pages to publish from the `main` branch and the `/docs` folder.
