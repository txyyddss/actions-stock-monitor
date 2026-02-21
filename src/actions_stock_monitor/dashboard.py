from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _h(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def _format_ts_short(ts: str | None) -> str:
    dt = _parse_iso(ts)
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def render_dashboard_html(state: dict[str, Any], *, run_summary: dict[str, Any] | None = None) -> str:
    run_summary = run_summary or {}
    updated_at = state.get("updated_at") or run_summary.get("finished_at") or ""

    products: list[dict[str, Any]] = []
    for _, p in (state.get("products") or {}).items():
        if not isinstance(p, dict):
            continue
        billing = p.get("billing_cycles") or []
        if isinstance(billing, list):
            billing = ", ".join(str(c) for c in billing)
        else:
            billing = str(billing) if billing else ""
        cycle_prices = p.get("cycle_prices") or {}
        if not isinstance(cycle_prices, dict):
            cycle_prices = {}
        locations = p.get("locations")
        if not isinstance(locations, list):
            base_loc = p.get("location") or p.get("option")
            locations = [str(base_loc)] if isinstance(base_loc, str) and base_loc else []
        else:
            locations = [str(x) for x in locations if isinstance(x, str) and x]
        location_links = p.get("location_links")
        if not isinstance(location_links, dict):
            location_links = {}

        products.append(
            {
                "domain": p.get("domain", ""),
                "name": p.get("name", ""),
                "price": p.get("price") or "",
                "available": p.get("available", None),
                "specs": p.get("specs") or {},
                "description": p.get("description") or "",
                "url": p.get("url") or "",
                "first_seen": p.get("first_seen") or "",
                "last_seen": p.get("last_seen") or "",
                "billing_cycles": billing,
                "cycle_prices": cycle_prices,
                "location": p.get("location") or p.get("option") or (locations[0] if locations else ""),
                "locations": locations,
                "location_links": location_links,
                "variant_of": p.get("variant_of") or "",
                "is_special": bool(p.get("is_special")),
            }
        )

    def avail_rank(v: Any) -> int:
        if v is True:
            return 0
        if v is False:
            return 1
        return 2

    products.sort(key=lambda x: (avail_rank(x["available"]), x["domain"], x["name"]))

    domains = state.get("domains") or {}
    domains_ok = sum(1 for d in domains.values() if isinstance(d, dict) and d.get("last_status") == "ok")
    domains_error = sum(1 for d in domains.values() if isinstance(d, dict) and d.get("last_status") == "error")

    in_stock_count = sum(1 for p in products if p["available"] is True)
    out_stock_count = sum(1 for p in products if p["available"] is False)
    unknown_count = len(products) - in_stock_count - out_stock_count

    data_json = json.dumps({"products": products}, ensure_ascii=False)
    data_json_safe = (
        data_json.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )

    run_started = _format_ts_short(run_summary.get("started_at"))
    run_finished = _format_ts_short(run_summary.get("finished_at"))

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Restock Monitor — Live VPS Stock Dashboard</title>
  <meta name="description" content="Real-time VPS hosting stock monitor dashboard tracking product availability across {len(products)} products from {domains_ok + domains_error} providers." />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
  <style>
    :root {{
      --bg: #0b0f1a;
      --bg2: #101827;
      --surface: rgba(255,255,255,0.04);
      --glass: rgba(255,255,255,0.06);
      --glass-border: rgba(255,255,255,0.1);
      --glass-hover: rgba(255,255,255,0.09);
      --txt: #eaf0ff;
      --txt2: rgba(234,240,255,0.65);
      --ok: #34d399;
      --ok-bg: rgba(52,211,153,0.12);
      --bad: #f87171;
      --bad-bg: rgba(248,113,113,0.12);
      --unk: #fbbf24;
      --unk-bg: rgba(251,191,36,0.12);
      --accent: #818cf8;
      --accent2: #38bdf8;
      --accent-glow: rgba(129,140,248,0.25);
      --special: #fbbf24;
      --radius: 16px;
      --radius-sm: 10px;
      --shadow: 0 8px 32px rgba(0,0,0,0.4);
      --transition: 0.2s cubic-bezier(0.4,0,0.2,1);
    }}
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      background: var(--bg);
      color: var(--txt);
      font-family: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
      line-height: 1.5;
      min-height: 100vh;
      overflow-x: hidden;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      background:
        radial-gradient(ellipse 900px 600px at 10% 0%, rgba(129,140,248,0.15), transparent),
        radial-gradient(ellipse 800px 500px at 90% 5%, rgba(56,189,248,0.1), transparent),
        radial-gradient(ellipse 600px 400px at 50% 100%, rgba(52,211,153,0.06), transparent);
      pointer-events: none;
      z-index: 0;
    }}
    a {{ color: var(--accent2); text-decoration: none; transition: color var(--transition); }}
    a:hover {{ color: var(--accent); }}
    .wrap {{ position: relative; z-index: 1; max-width: 1400px; margin: 0 auto; padding: 20px 16px 48px; }}

    /* ─── Header ────────────────────────────────── */
    .header {{
      background: linear-gradient(135deg, rgba(129,140,248,0.12), rgba(56,189,248,0.08), rgba(52,211,153,0.06));
      border: 1px solid var(--glass-border);
      border-radius: var(--radius);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      padding: 20px 24px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 12px;
      box-shadow: var(--shadow), inset 0 1px 0 rgba(255,255,255,0.06);
      animation: headerFadeIn 0.6s ease-out;
    }}
    @keyframes headerFadeIn {{
      from {{ opacity: 0; transform: translateY(-12px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    .header h1 {{
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.3px;
      background: linear-gradient(135deg, var(--txt) 0%, var(--accent2) 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }}
    .header-sub {{ color: var(--txt2); font-size: 13px; margin-top: 4px; line-height: 1.45; }}
    .header-sub b {{ color: var(--txt); }}
    .pills {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 12px;
      border-radius: 999px;
      border: 1px solid var(--glass-border);
      background: var(--glass);
      color: var(--txt2);
      font-size: 12px;
      font-weight: 500;
      white-space: nowrap;
      transition: all var(--transition);
    }}
    .pill:hover {{ background: var(--glass-hover); border-color: rgba(255,255,255,0.18); }}
    .pill b {{ color: var(--txt); }}
    .pill a {{ color: var(--accent2); font-weight: 600; }}

    /* ─── Stat Cards ────────────────────────────── */
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 10px;
      margin: 14px 0;
      animation: fadeUp 0.5s ease-out 0.1s both;
    }}
    @keyframes fadeUp {{
      from {{ opacity: 0; transform: translateY(8px); }}
      to {{ opacity: 1; transform: translateY(0); }}
    }}
    .stat-card {{
      background: var(--glass);
      border: 1px solid var(--glass-border);
      border-radius: var(--radius-sm);
      padding: 14px 16px;
      display: flex;
      flex-direction: column;
      gap: 4px;
      transition: all var(--transition);
    }}
    .stat-card:hover {{ background: var(--glass-hover); transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,0,0,0.3); }}
    .stat-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px; color: var(--txt2); font-weight: 600; }}
    .stat-value {{ font-size: 26px; font-weight: 700; font-family: "JetBrains Mono", monospace; }}
    .stat-ok .stat-value {{ color: var(--ok); }}
    .stat-bad .stat-value {{ color: var(--bad); }}
    .stat-unk .stat-value {{ color: var(--unk); }}
    .stat-total .stat-value {{ color: var(--accent2); }}

    /* ─── Chart row ─────────────────────────────── */
    .chart-row {{
      display: flex;
      gap: 12px;
      align-items: center;
      margin-bottom: 14px;
      animation: fadeUp 0.5s ease-out 0.15s both;
    }}
    .donut-wrap {{
      width: 100px;
      height: 100px;
      flex-shrink: 0;
    }}
    .donut-wrap svg {{ width: 100%; height: 100%; }}
    .bar-chart {{
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .bar-row {{ display: flex; align-items: center; gap: 8px; font-size: 12px; }}
    .bar-label {{ width: 72px; color: var(--txt2); font-weight: 500; text-align: right; }}
    .bar-track {{
      flex: 1;
      height: 8px;
      border-radius: 4px;
      background: rgba(255,255,255,0.06);
      overflow: hidden;
    }}
    .bar-fill {{
      height: 100%;
      border-radius: 4px;
      transition: width 0.6s cubic-bezier(0.4,0,0.2,1);
    }}
    .bar-fill-ok {{ background: linear-gradient(90deg, var(--ok), #6ee7b7); }}
    .bar-fill-bad {{ background: linear-gradient(90deg, var(--bad), #fca5a5); }}
    .bar-fill-unk {{ background: linear-gradient(90deg, var(--unk), #fde68a); }}
    .bar-pct {{ width: 36px; font-size: 11px; color: var(--txt2); font-family: "JetBrains Mono", monospace; }}

    /* ─── Controls ──────────────────────────────── */
    .controls {{
      position: sticky;
      top: 0;
      z-index: 20;
      background: rgba(11,15,26,0.85);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--glass-border);
      margin: 0 -16px;
      padding: 10px 16px;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      animation: fadeUp 0.5s ease-out 0.2s both;
    }}
    .search-wrap {{
      position: relative;
      flex: 1;
      min-width: 200px;
      max-width: 520px;
    }}
    .search-wrap::before {{
      content: "🔍";
      position: absolute;
      left: 12px;
      top: 50%;
      transform: translateY(-50%);
      font-size: 14px;
      pointer-events: none;
    }}
    .search-wrap input {{
      width: 100%;
      padding: 10px 12px 10px 36px;
      border-radius: var(--radius-sm);
      border: 1px solid var(--glass-border);
      background: rgba(0,0,0,0.3);
      color: var(--txt);
      font-family: inherit;
      font-size: 13px;
      outline: none;
      transition: all var(--transition);
    }}
    .search-wrap input:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }}
    select {{
      padding: 10px 12px;
      border-radius: var(--radius-sm);
      border: 1px solid var(--glass-border);
      background: rgba(0,0,0,0.3);
      color: var(--txt);
      font-family: inherit;
      font-size: 13px;
      outline: none;
      cursor: pointer;
      transition: all var(--transition);
    }}
    select:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }}
    .result-count {{
      color: var(--txt2);
      font-size: 12px;
      font-weight: 500;
      margin-left: auto;
      white-space: nowrap;
    }}

    /* ─── Table ─────────────────────────────────── */
    .table-wrap {{
      border: 1px solid var(--glass-border);
      border-radius: var(--radius);
      background: var(--glass);
      overflow: hidden;
      box-shadow: var(--shadow);
      margin-top: 12px;
      animation: fadeUp 0.5s ease-out 0.25s both;
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead th {{
      position: sticky;
      top: 0;
      z-index: 10;
      background: rgba(11,15,26,0.95);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid var(--glass-border);
      color: var(--txt2);
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      padding: 12px 12px;
      text-align: left;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
      transition: color var(--transition);
    }}
    thead th:hover {{ color: var(--txt); }}
    thead th.sorted {{ color: var(--accent); }}
    thead th .sort-arrow {{ font-size: 10px; margin-left: 4px; opacity: 0.7; }}
    tbody td {{
      border-bottom: 1px solid rgba(255,255,255,0.05);
      padding: 10px 12px;
      font-size: 13px;
      vertical-align: top;
      transition: background var(--transition);
    }}
    tbody tr {{ transition: background var(--transition); }}
    tbody tr:hover td {{ background: rgba(129,140,248,0.06); }}
    tbody tr.row-in td {{ border-left: 3px solid var(--ok); }}
    tbody tr.row-out td {{ border-left: 3px solid var(--bad); }}
    tbody tr.row-unk td {{ border-left: 3px solid transparent; }}

    .status-badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
      white-space: nowrap;
    }}
    .status-in {{ background: var(--ok-bg); color: var(--ok); }}
    .status-out {{ background: var(--bad-bg); color: var(--bad); }}
    .status-unk {{ background: var(--unk-bg); color: var(--unk); }}
    .dot {{ width: 7px; height: 7px; border-radius: 50%; display: inline-block; flex-shrink: 0; }}
    .dot-ok {{ background: var(--ok); box-shadow: 0 0 6px var(--ok); }}
    .dot-bad {{ background: var(--bad); box-shadow: 0 0 6px var(--bad); }}
    .dot-unk {{ background: var(--unk); box-shadow: 0 0 6px var(--unk); }}
    .muted {{ color: var(--txt2); }}
    .domain-cell {{ font-family: "JetBrains Mono", monospace; font-size: 12px; color: var(--txt2); }}
    .product-name {{ font-weight: 600; color: var(--txt); }}
    .product-name:hover {{ color: var(--accent2); }}
    .tag {{
      display: inline-block;
      padding: 2px 7px;
      border-radius: 6px;
      font-size: 10px;
      font-weight: 600;
      margin-left: 6px;
      vertical-align: middle;
    }}
    .tag-location {{ background: rgba(56,189,248,0.12); color: var(--accent2); border: 1px solid rgba(56,189,248,0.25); }}
    .tag-special {{ background: rgba(251,191,36,0.14); color: var(--special); border: 1px solid rgba(251,191,36,0.3); }}
    .chip {{
      display: inline-block;
      padding: 2px 7px;
      border-radius: 6px;
      background: rgba(255,255,255,0.05);
      border: 1px solid rgba(255,255,255,0.08);
      font-size: 11px;
      color: var(--txt2);
      margin: 2px 3px 0 0;
    }}
    .variant-info {{ font-size: 11px; color: var(--txt2); margin-top: 2px; }}
    .desc-toggle {{
      margin-top: 6px;
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 8px;
      overflow: hidden;
      background: rgba(0,0,0,0.2);
    }}
    .desc-toggle summary {{
      padding: 5px 8px;
      font-size: 11px;
      color: var(--txt2);
      cursor: pointer;
      transition: color var(--transition);
    }}
    .desc-toggle summary:hover {{ color: var(--txt); }}
    .desc-box {{ padding: 6px 8px; font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; color: var(--txt2); }}
    .price-cell {{ font-family: "JetBrains Mono", monospace; font-weight: 600; font-size: 13px; }}
    .cycle-sub {{ font-size: 11px; color: var(--txt2); margin-top: 3px; }}
    .cycle-sub div {{ margin-bottom: 1px; }}
    .btn {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 6px 12px;
      border-radius: 8px;
      font-size: 11px;
      font-weight: 600;
      white-space: nowrap;
      border: 1px solid rgba(129,140,248,0.35);
      background: linear-gradient(135deg, rgba(129,140,248,0.15), rgba(56,189,248,0.1));
      color: var(--txt);
      transition: all var(--transition);
      text-decoration: none;
    }}
    .btn:hover {{ border-color: var(--accent); box-shadow: 0 0 12px var(--accent-glow); transform: translateY(-1px); text-decoration: none; }}
    .empty-state {{
      text-align: center;
      padding: 48px 16px;
      color: var(--txt2);
      font-size: 14px;
    }}
    .empty-state .empty-icon {{ font-size: 40px; margin-bottom: 12px; }}

    /* ─── Footer ────────────────────────────────── */
    .footer {{
      text-align: center;
      padding: 24px 0 8px;
      color: var(--txt2);
      font-size: 11px;
    }}

    /* ─── Responsive ────────────────────────────── */
    @media (max-width: 800px) {{
      .header {{ flex-direction: column; align-items: flex-start; }}
      .chart-row {{ flex-direction: column; align-items: stretch; }}
      .donut-wrap {{ align-self: center; }}
      thead {{ display: none; }}
      table, tbody, tr, td {{ display: block; width: 100%; }}
      tbody tr {{
        border: 1px solid var(--glass-border);
        border-radius: var(--radius-sm);
        margin-bottom: 8px;
        padding: 4px 0;
        background: var(--glass);
      }}
      tbody td {{
        border-bottom: none;
        padding: 6px 12px;
      }}
      tbody td[data-k]::before {{
        content: attr(data-k);
        display: block;
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
        color: var(--txt2);
        margin-bottom: 3px;
        font-weight: 600;
      }}
      tbody tr.row-in, tbody tr.row-out, tbody tr.row-unk {{ border-left-width: 3px !important; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="header">
      <div>
        <h1>📡 Restock Monitor</h1>
        <div class="header-sub">
          Last updated: <b>{_h(updated_at)}</b><br/>
          Tracking <b>{len(products)}</b> products across <b>{domains_ok + domains_error}</b> providers
        </div>
      </div>
      <div class="pills">
        <div class="pill"><a href="https://t.me/tx_stock_monitor" target="_blank" rel="noreferrer noopener">📢 Telegram</a></div>
        <div class="pill">Restocks: <b>{_h(run_summary.get("restocks", 0))}</b></div>
        <div class="pill">New: <b>{_h(run_summary.get("new_products", 0))}</b></div>
        <div class="pill">🕐 {_h(run_started)} → {_h(run_finished)}</div>
      </div>
    </header>

    <div class="stats-grid">
      <div class="stat-card stat-ok"><div class="stat-label">In Stock</div><div class="stat-value" id="cOk">{in_stock_count}</div></div>
      <div class="stat-card stat-bad"><div class="stat-label">Out of Stock</div><div class="stat-value" id="cBad">{out_stock_count}</div></div>
      <div class="stat-card stat-unk"><div class="stat-label">Unknown</div><div class="stat-value" id="cUnk">{unknown_count}</div></div>
      <div class="stat-card stat-total"><div class="stat-label">Total Products</div><div class="stat-value" id="cTot">{len(products)}</div></div>
    </div>

    <div class="chart-row">
      <div class="donut-wrap">
        <svg viewBox="0 0 42 42" id="pie" role="img" aria-label="Stock distribution">
          <circle cx="21" cy="21" r="15.9" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="5"/>
          <circle id="arc-ok" cx="21" cy="21" r="15.9" fill="none" stroke="var(--ok)" stroke-width="5"
                  stroke-dasharray="0 100" stroke-dashoffset="25" stroke-linecap="round" style="transition:stroke-dasharray 0.8s ease"/>
          <circle id="arc-bad" cx="21" cy="21" r="15.9" fill="none" stroke="var(--bad)" stroke-width="5"
                  stroke-dasharray="0 100" stroke-dashoffset="25" stroke-linecap="round" style="transition:stroke-dasharray 0.8s ease"/>
          <circle id="arc-unk" cx="21" cy="21" r="15.9" fill="none" stroke="var(--unk)" stroke-width="5"
                  stroke-dasharray="0 100" stroke-dashoffset="25" stroke-linecap="round" style="transition:stroke-dasharray 0.8s ease"/>
        </svg>
      </div>
      <div class="bar-chart">
        <div class="bar-row"><span class="bar-label">✅ In Stock</span><div class="bar-track"><div class="bar-fill bar-fill-ok" id="barOk" style="width:0%"></div></div><span class="bar-pct" id="pctOk">0%</span></div>
        <div class="bar-row"><span class="bar-label">❌ Out</span><div class="bar-track"><div class="bar-fill bar-fill-bad" id="barBad" style="width:0%"></div></div><span class="bar-pct" id="pctBad">0%</span></div>
        <div class="bar-row"><span class="bar-label">❓ Unknown</span><div class="bar-track"><div class="bar-fill bar-fill-unk" id="barUnk" style="width:0%"></div></div><span class="bar-pct" id="pctUnk">0%</span></div>
      </div>
    </div>

    <div class="controls">
      <div class="search-wrap">
        <input id="q" type="search" placeholder="Search products, domains, specs, prices…" autocomplete="off" />
      </div>
      <select id="site" aria-label="Site filter">
        <option value="">All Sites</option>
      </select>
      <select id="stock-filter" aria-label="Stock filter">
        <option value="">All Stock</option>
        <option value="in">✅ In Stock</option>
        <option value="out">❌ Out of Stock</option>
        <option value="unknown">❓ Unknown</option>
      </select>
      <span class="result-count" id="resultCount"></span>
      <span class="muted" style="font-size:11px">Click headers to sort.</span>
    </div>

    <div class="table-wrap">
      <table id="t">
        <thead>
          <tr>
            <th data-col="available">Status</th>
            <th data-col="domain">Domain</th>
            <th data-col="name">Product</th>
            <th data-col="price">Price</th>
            <th data-col="billing_cycles">Cycles</th>
            <th data-col="last_seen">Last Seen</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody id="tb"></tbody>
      </table>
    </div>

    <div class="footer">
      Powered by Actions Stock Monitor · Data refreshes automatically via GitHub Actions
    </div>
  </div>

  <script id="dashboard-data" type="application/json">{data_json_safe}</script>
  <script>
    "use strict";
    let DATA;
    try {{
      const d = document.getElementById("dashboard-data");
      DATA = JSON.parse(d && d.textContent ? d.textContent : '{{"products":[]}}');
    }} catch (_) {{
      DATA = {{ products: [] }};
    }}

    /* Pre-compute search blobs once */
    DATA.products.forEach(p => {{
      const spec = Object.entries(p.specs||{{}}).map(([k,v])=>k+":"+v).join(" ");
      const cp = Object.entries(p.cycle_prices||{{}}).map(([k,v])=>k+":"+v).join(" ");
      const locs = Array.isArray(p.locations) ? p.locations.join(" ") : "";
      p._blob = `${{p.domain}} ${{p.name}} ${{p.price}} ${{p.description||""}} ${{spec}} ${{p.url}} ${{p.billing_cycles||""}} ${{p.location||""}} ${{locs}} ${{cp}}`.toLowerCase();
    }});

    const tb = document.getElementById("tb");
    const q = document.getElementById("q");
    const site = document.getElementById("site");
    const stockFilter = document.getElementById("stock-filter");
    const table = document.getElementById("t");
    const resultCount = document.getElementById("resultCount");

    let sortCol = "available", sortDir = 1;

    function statusMeta(a) {{
      if (a===true) return {{cls:"in",dot:"ok",label:"In Stock"}};
      if (a===false) return {{cls:"out",dot:"bad",label:"Out of Stock"}};
      return {{cls:"unk",dot:"unk",label:"Unknown"}};
    }}

    function cmp(a,b) {{
      const av=a[sortCol], bv=b[sortCol];
      if (sortCol==="available") {{
        const r=v=>(v===true?0:(v===false?1:2));
        return (r(av)-r(bv))*sortDir;
      }}
      return String(av??"").localeCompare(String(bv??""),undefined,{{numeric:true,sensitivity:"base"}})*sortDir;
    }}

    function updateCharts(items) {{
      const t=items.length||1;
      const ok=items.filter(p=>p.available===true).length;
      const bad=items.filter(p=>p.available===false).length;
      const unk=t-ok-bad;
      const total=items.length;

      document.getElementById("cOk").textContent=ok;
      document.getElementById("cBad").textContent=bad;
      document.getElementById("cUnk").textContent=unk;
      document.getElementById("cTot").textContent=total;

      const pOk=total?(ok/total*100):0;
      const pBad=total?(bad/total*100):0;
      const pUnk=total?(unk/total*100):0;

      /* SVG donut arcs */
      const C=100;
      const aOk=pOk/100*C;
      const aBad=pBad/100*C;
      const aUnk=pUnk/100*C;
      document.getElementById("arc-ok").setAttribute("stroke-dasharray",aOk+" "+(C-aOk));
      document.getElementById("arc-ok").setAttribute("stroke-dashoffset","25");
      document.getElementById("arc-bad").setAttribute("stroke-dasharray",aBad+" "+(C-aBad));
      document.getElementById("arc-bad").setAttribute("stroke-dashoffset",String(25-aOk));
      document.getElementById("arc-unk").setAttribute("stroke-dasharray",aUnk+" "+(C-aUnk));
      document.getElementById("arc-unk").setAttribute("stroke-dashoffset",String(25-aOk-aBad));

      /* Bars */
      document.getElementById("barOk").style.width=pOk+"%";
      document.getElementById("barBad").style.width=pBad+"%";
      document.getElementById("barUnk").style.width=pUnk+"%";
      document.getElementById("pctOk").textContent=Math.round(pOk)+"%";
      document.getElementById("pctBad").textContent=Math.round(pBad)+"%";
      document.getElementById("pctUnk").textContent=Math.round(pUnk)+"%";
    }}

    function esc(s) {{
      return String(s??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
    }}

    function render() {{
      const needle = (q.value||"").trim().toLowerCase();
      const siteV = site.value||"";
      const stockV = stockFilter.value||"";
      const items = DATA.products.filter(p => {{
        if (siteV && p.domain!==siteV) return false;
        if (stockV==="in" && p.available!==true) return false;
        if (stockV==="out" && p.available!==false) return false;
        if (stockV==="unknown" && p.available!=null) return false;
        if (needle && !p._blob.includes(needle)) return false;
        return true;
      }}).sort(cmp);

      updateCharts(items);
      resultCount.textContent = items.length + " product" + (items.length===1?"":"s");

      table.querySelectorAll("thead th[data-col]").forEach(th => {{
        const col=th.getAttribute("data-col");
        th.classList.toggle("sorted", col===sortCol);
        const base=th.textContent.replace(/\\s*[↑↓]$/,"");
        th.innerHTML=col===sortCol ? esc(base)+'<span class="sort-arrow">'+(sortDir===1?"↑":"↓")+"</span>" : esc(base);
      }});

      const frag=document.createDocumentFragment();
      if (items.length===0) {{
        const tr=document.createElement("tr");
        tr.innerHTML='<td colspan="7"><div class="empty-state"><div class="empty-icon">📦</div>No products match your filters</div></td>';
        frag.appendChild(tr);
      }} else {{
        for (const p of items) {{
          const m=statusMeta(p.available);
          const tr=document.createElement("tr");
          tr.className="row-"+m.cls;

          const specs=Object.entries(p.specs||{{}}).map(([k,v])=>'<span class="chip">'+esc(k)+": "+esc(v)+"</span>").join("");
          const desc=p.description?'<details class="desc-toggle"><summary>Details</summary><div class="desc-box">'+esc(p.description)+"</div></details>":"";
          const locs = Array.isArray(p.locations) && p.locations.length ? p.locations : (p.location ? [p.location] : []);
          const locTag = locs.slice(0,3).map(x => '<span class="tag tag-location">'+esc(x)+"</span>").join("") + (locs.length>3?'<span class="tag tag-location">+'+String(locs.length-3)+' more</span>':"");
          const spTag=p.is_special?'<span class="tag tag-special">Special</span>':"";
          const variant=p.variant_of?'<div class="variant-info">Plan: '+esc(p.variant_of)+"</div>":"";
          const cycles=p.billing_cycles?esc(p.billing_cycles):'<span class="muted">—</span>';
          const cpHtml=Object.entries(p.cycle_prices||{{}}).map(([k,v])=>"<div><span class='muted'>"+esc(k)+":</span> "+esc(v)+"</div>").join("");
          const price=p.price?'<div class="price-cell">'+esc(p.price)+"</div>"+(cpHtml?'<div class="cycle-sub">'+cpHtml+"</div>":""):'<span class="muted">—</span>';

          tr.innerHTML=`
            <td data-k="Status"><span class="status-badge status-${{m.cls}}"><span class="dot dot-${{m.dot}}"></span>${{m.label}}</span></td>
            <td data-k="Domain"><span class="domain-cell">${{esc(p.domain)}}</span></td>
            <td data-k="Product">
              <div><a class="product-name" href="${{esc(p.url)}}" target="_blank" rel="noreferrer noopener">${{esc(p.name)}}</a>${{locTag}}${{spTag}}</div>
              ${{variant}}${{desc}}<div>${{specs}}</div>
            </td>
            <td data-k="Price">${{price}}</td>
            <td data-k="Cycles">${{cycles}}</td>
            <td data-k="Last Seen"><span class="muted" style="font-size:12px">${{esc(p.last_seen||"")}}</span></td>
            <td data-k="Action"><a class="btn" href="${{esc(p.url)}}" target="_blank" rel="noreferrer noopener">🛒 Buy Now</a></td>
          `;
          frag.appendChild(tr);
        }}
      }}
      tb.innerHTML="";
      tb.appendChild(frag);
    }}

    /* Debounced search */
    let _searchTimer;
    q.addEventListener("input", () => {{ clearTimeout(_searchTimer); _searchTimer = setTimeout(render, 150); }});
    site.addEventListener("change", render);
    stockFilter.addEventListener("change", render);

    table.querySelectorAll("thead th[data-col]").forEach(th => {{
      th.addEventListener("click", () => {{
        const col=th.getAttribute("data-col");
        if (!col) return;
        if (sortCol===col) sortDir*=-1;
        else {{ sortCol=col; sortDir=1; }}
        render();
      }});
    }});

    /* Populate site filter */
    const domains=Array.from(new Set(DATA.products.map(p=>p.domain).filter(Boolean))).sort();
    for (const d of domains) {{
      const o=document.createElement("option");
      o.value=d; o.textContent=d;
      site.appendChild(o);
    }}

    render();
  </script>
</body>
</html>
"""
