# Actions Stock Monitor

Restock monitor + static dashboard generator intended to run on a schedule via GitHub Actions.

## Setup (GitHub Secrets)

Add these repository secrets:

- `TELEGRAM_BOT_TOKEN` (required to send notifications)
- `TELEGRAM_CHAT_ID` (required to send notifications)
- `PROXY_URL` (optional; supports `socks5://...` or `http(s)://...`)
- `FLARESOLVERR_URL` (optional; e.g. `http://127.0.0.1:8191`)

## Run locally

```powershell
pip install -r requirements.txt
pip install -e .
python -m actions_stock_monitor --state data/state.json --output docs/index.html --dry-run
```

## GitHub Pages

This repo writes the dashboard to `docs/index.html`. Configure GitHub Pages to publish from the `main` branch and `/docs` folder.
