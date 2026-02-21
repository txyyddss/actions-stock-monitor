# Actions Stock Monitor

Track VPS product stock/restocks across multiple providers, send Telegram alerts, and generate a static dashboard (`docs/index.html`).

## Quick Start

1. Install:
```powershell
pip install -r requirements.txt
pip install -e .
```

2. (Optional) run FlareSolverr:
```powershell
docker run --rm -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest
```

3. Run monitor once (dry run):
```powershell
$env:FLARESOLVERR_URL="http://127.0.0.1:8191/"
python -m actions_stock_monitor --state data/state.json --output docs/index.html --dry-run --mode full
```

## Single-Site Debug Workflow (Required)

Run **one site at a time** to avoid timeouts.

1. Simple crawl:
```powershell
python scripts/site_debug.py --target https://example.com/ --stage simple --flaresolverr-url http://127.0.0.1:8191/
```

2. Manually inspect origin HTML/API and baseline list in `data/debug/<domain>/<timestamp>/manual_baseline.json`.

3. Monitor stage:
```powershell
python scripts/site_debug.py --target https://example.com/ --stage monitor --flaresolverr-url http://127.0.0.1:8191/
```

4. Compare outputs:
- `raw_pages.json`
- `parsed_simple.json`
- `parsed_monitor.json`
- `diff_report.json`

5. Clean temp artifacts:
```powershell
Remove-Item -Recurse -Force data/debug/*
```

## Runtime Modes

- `full`: full discovery/enrichment, missing-product pruning, removed-domain pruning (when not using explicit `--targets`).
- `lite`: state-driven subset, no expansion, no pruning.

## Main Env Knobs

### Speed / Throughput
- `MAX_WORKERS` (target-level concurrency)
- `TARGET_MAX_DURATION_SECONDS`
- `DISCOVERY_MAX_PAGES_PER_DOMAIN`
- `DISCOVERY_WORKERS`, `DISCOVERY_BATCH`
- `ENRICH_WORKERS`
- `PARALLEL_SIMPLE_HIDDEN` (`1` default; run simple crawler + hidden scanner concurrently per target)

### Discovery Accuracy
- `DISCOVERY_MAX_PRODUCTS_PER_DOMAIN`
- `DISCOVERY_STOP_AFTER_NO_NEW_PAGES`
- `DISCOVERY_STOP_AFTER_FETCH_ERRORS`
- `DISCOVERY_STRICT_FETCH_ERROR_STOP` (`0` default for WHMCS-like behavior)
- `DISCOVERY_FORCE_IF_PRODUCTS_LEQ`
- `DISCOVERY_FORCE_IF_PRIMARY_LISTING_PRODUCTS_LEQ`

### Hidden WHMCS Scanner
- Hidden scan supports both WHMCS (`pid`/`gid`) and HostBill-like (`id`/`fid`) carts using the same `WHMCS_HIDDEN_*` knobs.
- Brute scans start from `0` for both product IDs and group/category IDs.
- Hidden-scan discoveries are kept regardless of stock state (`in stock` / `out of stock` / `unknown`).
- `WHMCS_HIDDEN_MAX_DURATION_SECONDS`
- `WHMCS_HIDDEN_WORKERS`, `WHMCS_HIDDEN_BATCH`
- `WHMCS_HIDDEN_HARD_MAX_PID`, `WHMCS_HIDDEN_HARD_MAX_GID`
- `WHMCS_HIDDEN_PID_STOP_AFTER_NO_INFO`
- `WHMCS_HIDDEN_GID_STOP_AFTER_SAME_PAGE` (default `12`)
- `WHMCS_HIDDEN_PID_STOP_AFTER_NO_PROGRESS`
- `WHMCS_HIDDEN_GID_STOP_AFTER_NO_PROGRESS`
- `WHMCS_HIDDEN_PID_STOP_AFTER_DUPLICATES` (default `40`)
- `WHMCS_HIDDEN_GID_STOP_AFTER_DUPLICATES` (default `40`)
- `WHMCS_HIDDEN_REDIRECT_SIGNATURE_STOP_AFTER` (default `36`)
- `WHMCS_HIDDEN_LOG`

### Cloudflare / Network
- `FLARESOLVERR_URL`
- `TIMEOUT_SECONDS`
- `PROXY_URL`
- `CF_COOKIE_TTL_SECONDS`

### Telegram
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_MAX_RETRIES`
- `TELEGRAM_RETRY_BASE_SECONDS`
- `TELEGRAM_MIN_INTERVAL_SECONDS`

## Troubleshooting By Symptom

### Missing products
- Run single-site debug workflow.
- Increase discovery pages/workers.
- Disable strict fetch-error stop (`DISCOVERY_STRICT_FETCH_ERROR_STOP=0`).
- Check domain-specific extra pages and discovery candidates.

### False positives
- Validate product URL patterns in debug output.
- Check domain cleanup rules in `monitor.py`.
- Confirm non-product URLs are filtered.

### Missing specs/tags
- Inspect raw card HTML in `raw_pages.json`.
- Improve parser key/value extraction and `extract_specs` patterns.

### Cycle prices missing
- Verify `billing_cycles` and `cycle_prices` on detail pages.
- Ensure enrichment is enabled and page budget is sufficient.

### Telegram name split/repeat
- Validate `_format_message` output with `tests/test_monitor_message.py`.

### Dashboard slow with large data
- Use built-in pagination and filters.
- Filter by site/stock/price/special before deep search.

## Notes

- Explicit target runs (`--targets`) are scoped debug runs; cross-domain prune is intentionally disabled.
- Location extraction is explicit-only (location/datacenter/region/zone/node fields), not inferred from product/category names.
