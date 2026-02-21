from __future__ import annotations

import html
import json
import re
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


def _price_to_float(value: str | None) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    m = re.search(r"(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)", text)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        return float(raw)
    except Exception:
        return None


def render_dashboard_html(state: dict[str, Any], *, run_summary: dict[str, Any] | None = None) -> str:
    run_summary = run_summary or {}
    updated_at = state.get("updated_at") or run_summary.get("finished_at") or ""

    products: list[dict[str, Any]] = []
    for _, p in (state.get("products") or {}).items():
        if not isinstance(p, dict):
            continue
        billing_cycles = p.get("billing_cycles") or []
        if not isinstance(billing_cycles, list):
            billing_cycles = [str(billing_cycles)] if billing_cycles else []

        cycle_prices = p.get("cycle_prices") or {}
        if not isinstance(cycle_prices, dict):
            cycle_prices = {}

        price_text = str(p.get("price") or "")
        price_value = _price_to_float(price_text)
        if price_value is None and cycle_prices:
            preferred_order = ["Monthly", "Quarterly", "Semiannual", "Yearly", "Biennial", "Triennial", "One-Time"]
            candidates: list[str] = []
            for key in preferred_order:
                if key in cycle_prices:
                    candidates.append(str(cycle_prices[key]))
            for _, v in cycle_prices.items():
                candidates.append(str(v))
            for c in candidates:
                parsed = _price_to_float(c)
                if parsed is not None:
                    price_value = parsed
                    if not price_text:
                        price_text = c
                    break

        locations = p.get("locations")
        if not isinstance(locations, list):
            base_loc = p.get("location") or p.get("option")
            locations = [str(base_loc)] if isinstance(base_loc, str) and base_loc else []
        else:
            locations = [str(x) for x in locations if isinstance(x, str) and x]

        location_links = p.get("location_links")
        if not isinstance(location_links, dict):
            location_links = {}

        specs_raw = p.get("specs") or {}
        specs: dict[str, str] = {}
        if isinstance(specs_raw, dict):
            for k, v in specs_raw.items():
                ks = str(k)
                if ks.strip().lower() == "cycles":
                    continue
                specs[ks] = str(v)

        products.append(
            {
                "domain": str(p.get("domain") or ""),
                "name": str(p.get("name") or ""),
                "original_name": str(p.get("raw", {}).get("name") if isinstance(p.get("raw"), dict) else "") or str(p.get("name") or ""),
                "price": price_text,
                "price_value": price_value,
                "available": p.get("available", None),
                "specs": specs,
                "description": str(p.get("description") or ""),
                "original_description": str(p.get("raw", {}).get("description") if isinstance(p.get("raw"), dict) else "") or str(p.get("description") or ""),
                "url": str(p.get("url") or ""),
                "first_seen": str(p.get("first_seen") or ""),
                "last_seen": str(p.get("last_seen") or ""),
                "billing_cycles": billing_cycles,
                "cycle_prices": cycle_prices,
                "location": str(p.get("location") or p.get("option") or (locations[0] if locations else "")),
                "locations": locations,
                "location_links": location_links,
                "variant_of": str(p.get("variant_of") or ""),
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
  <title>Restock Monitor - Live VPS Stock Dashboard</title>
  <meta name="description" content="Real-time VPS hosting stock monitor dashboard tracking product availability across {len(products)} products from {domains_ok + domains_error} providers." />
  <style>
    :root {{
      --bg: #09090b;
      --surface: #18181b;
      --panel: #27272a;
      --line: #3f3f46;
      --txt: #f4f4f5;
      --muted: #a1a1aa;
      --ok: #22c55e;
      --bad: #ef4444;
      --unk: #eab308;
      --accent: #3b82f6;
      --special: #f59e0b;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font: 14px/1.5 "Inter", "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--txt); }}
    .wrap {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
    .header {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 24px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1); }}
    h1 {{ margin: 0; font-size: 28px; font-weight: 700; tracking: -0.025em; }}
    .sub {{ color: var(--muted); margin-top: 8px; font-size: 14px; }}
    .stats {{ display: grid; grid-template-columns: repeat(4, minmax(120px,1fr)); gap: 16px; margin: 24px 0; }}
    .card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 16px; box-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1); }}
    .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; font-weight: 600; letter-spacing: 0.05em; }}
    .num {{ font-size: 32px; font-weight: 700; margin-top: 4px; }}
    .ok {{ color: var(--ok); }} .bad {{ color: var(--bad); }} .unk {{ color: var(--unk); }}
    .controls {{ position: sticky; top: 16px; z-index: 10; background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 16px; display: grid; grid-template-columns: 2fr repeat(5, minmax(120px, 1fr)); gap: 12px; align-items: center; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); }}
    input, select {{ width: 100%; border: 1px solid var(--line); border-radius: 8px; background: var(--bg); color: var(--txt); padding: 10px 12px; font-size: 14px; outline: none; transition: border-color 0.2s; }}
    input:focus, select:focus {{ border-color: var(--accent); }}
    .hint {{ color: var(--muted); font-size: 13px; text-align: right; }}
    .table-wrap {{ margin-top: 24px; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: var(--surface); box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); }}
    table {{ width: 100%; border-collapse: collapse; text-align: left; }}
    th, td {{ padding: 12px 16px; border-bottom: 1px solid var(--line); vertical-align: top; }}
    th {{ text-transform: uppercase; color: var(--muted); font-size: 12px; font-weight: 600; cursor: pointer; user-select: none; background: var(--panel); position: sticky; top: 0; z-index: 2; letter-spacing: 0.05em; }}
    tr:last-child td {{ border-bottom: none; }}
    tr:hover td {{ background: var(--panel); }}
    .status {{ font-weight: 600; white-space: nowrap; }}
    .s-in {{ color: var(--ok); }} .s-out {{ color: var(--bad); }} .s-unk {{ color: var(--unk); }}
    .domain {{ color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 13px; }}
    .name a {{ color: var(--txt); text-decoration: none; font-weight: 600; font-size: 15px; }}
    .name a:hover {{ color: var(--accent); text-decoration: underline; }}
    .tag {{ display: inline-flex; align-items: center; border: 1px solid var(--line); border-radius: 9999px; padding: 2px 10px; margin-left: 8px; font-size: 11px; font-weight: 500; color: var(--txt); background: var(--panel); }}
    .tag-special {{ color: var(--special); background: rgba(245,158,11,0.1); border-color: rgba(245,158,11,0.2); }}
    .chip {{ display: inline-flex; border: 1px solid var(--line); background: var(--panel); border-radius: 6px; padding: 2px 8px; margin: 4px 6px 0 0; color: var(--txt); font-size: 12px; font-weight: 500; }}
    .cycles {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
    .btn {{ display: inline-flex; justify-content: center; align-items: center; background: var(--accent); color: white; border-radius: 8px; padding: 8px 16px; text-decoration: none; font-size: 13px; font-weight: 600; transition: background-color 0.2s; white-space: nowrap; }}
    .btn:hover {{ background: #2563eb; }}
    .pager {{ display: flex; justify-content: space-between; align-items: center; padding: 16px; color: var(--muted); background: var(--surface); }}
    .pager button {{ border: 1px solid var(--line); background: var(--panel); color: var(--txt); border-radius: 8px; padding: 8px 16px; font-weight: 500; cursor: pointer; transition: background-color 0.2s; }}
    .pager button:hover:not(:disabled) {{ background: var(--line); }}
    .pager button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    .empty {{ text-align: center; color: var(--muted); padding: 48px; font-size: 16px; }}

    @media (max-width: 1024px) {{
      .stats {{ grid-template-columns: repeat(2, minmax(120px,1fr)); }}
      .controls {{ grid-template-columns: 1fr 1fr; position: static; }}
      .hint {{ text-align: left; grid-column: 1 / -1; }}
      table, thead, tbody, tr, td {{ display: block; width: 100%; top: auto;}}
      thead {{ display: none; }}
      .table-wrap {{ background: transparent; border: 0; box-shadow: none; }}
      tbody tr {{ border: 1px solid var(--line); border-radius: 12px; background: var(--surface); margin-bottom: 16px; overflow: hidden; box-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1); }}
      tbody td {{ border-bottom: 1px solid var(--line); padding: 12px 16px; text-align: right; display: flex; justify-content: space-between; align-items: center; }}
      td[data-k]::before {{ content: attr(data-k); color: var(--muted); font-size: 12px; text-transform: uppercase; font-weight: 600; }}
      td[data-k="Product"] {{ flex-direction: column; align-items: flex-start; text-align: left; }}
      td[data-k="Product"]::before {{ margin-bottom: 8px; }}
      .name a {{ font-size: 16px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header class="header">
      <h1>Restock Monitor</h1>
      <div class="sub">Last updated: <b>{_h(updated_at)}</b> | Tracking <b>{len(products)}</b> products across <b>{domains_ok + domains_error}</b> providers</div>
      <div class="sub">Run: {_h(run_started)} -> {_h(run_finished)} | Restocks: <b>{_h(run_summary.get("restocks", 0))}</b> | New: <b>{_h(run_summary.get("new_products", 0))}</b></div>
    </header>

    <section class="stats">
      <div class="card"><div class="label">In Stock</div><div id="cOk" class="num ok">{in_stock_count}</div></div>
      <div class="card"><div class="label">Out of Stock</div><div id="cBad" class="num bad">{out_stock_count}</div></div>
      <div class="card"><div class="label">Unknown</div><div id="cUnk" class="num unk">{unknown_count}</div></div>
      <div class="card"><div class="label">Total Products</div><div id="cTot" class="num">{len(products)}</div></div>
    </section>

    <section class="controls">
      <input id="q" type="search" placeholder="Search products, domains, specs, prices..." autocomplete="off" />
      <select id="site" aria-label="Site filter"><option value="">All Sites</option></select>
      <select id="stock-filter" aria-label="Stock filter">
        <option value="">All Stock</option>
        <option value="in">In Stock</option>
        <option value="out">Out of Stock</option>
        <option value="unknown">Unknown</option>
      </select>
      <input id="min-price" type="number" step="0.01" min="0" placeholder="Min Price" aria-label="Min price" />
      <input id="max-price" type="number" step="0.01" min="0" placeholder="Max Price" aria-label="Max price" />
      <select id="special-filter" aria-label="Special filter">
        <option value="all">All Specials</option>
        <option value="only">Only Special</option>
        <option value="exclude">Exclude Special</option>
      </select>
      <div id="resultCount" class="hint">Click headers to sort.</div>
    </section>

    <div class="table-wrap">
      <table id="t">
        <thead>
          <tr>
            <th data-col="available">Status</th>
            <th data-col="domain">Domain</th>
            <th data-col="name">Product</th>
            <th data-col="price_value">Price</th>
            <th data-col="billing_cycles">Cycles</th>
            <th data-col="last_seen">Last Seen</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody id="tb"></tbody>
      </table>
      <div class="pager">
        <button id="prevPage" type="button">Prev</button>
        <span id="pageMeta"></span>
        <button id="nextPage" type="button">Next</button>
      </div>
    </div>
  </div>

  <script id="dashboard-data" type="application/json">{data_json_safe}</script>
  <script>
    "use strict";
    const PAGE_SIZE = 100;
    let DATA;
    try {{
      const d = document.getElementById("dashboard-data");
      DATA = JSON.parse(d && d.textContent ? d.textContent : '{{"products":[]}}');
    }} catch (_) {{
      DATA = {{ products: [] }};
    }}

    DATA.products.forEach(p => {{
      const spec = Object.entries(p.specs||{{}}).map(([k,v]) => k+":"+v).join(" ");
      const cp = Object.entries(p.cycle_prices||{{}}).map(([k,v]) => k+":"+v).join(" ");
      const locs = Array.isArray(p.locations) ? p.locations.join(" ") : "";
      p._blob = (`${{p.domain}} ${{p.name}} ${{p.original_name||""}} ${{p.price}} ${{p.description||""}} ${{p.original_description||""}} ${{spec}} ${{cp}} ${{locs}} ${{p.url}}`).toLowerCase();
    }});

    const tb = document.getElementById("tb");
    const q = document.getElementById("q");
    const site = document.getElementById("site");
    const stockFilter = document.getElementById("stock-filter");
    const minPrice = document.getElementById("min-price");
    const maxPrice = document.getElementById("max-price");
    const specialFilter = document.getElementById("special-filter");
    const resultCount = document.getElementById("resultCount");
    const prevPage = document.getElementById("prevPage");
    const nextPage = document.getElementById("nextPage");
    const pageMeta = document.getElementById("pageMeta");
    const table = document.getElementById("t");

    let sortCol = "available";
    let sortDir = 1;
    let page = 1;

    function statusMeta(v) {{
      if (v === true) return {{ cls: "s-in", label: "In Stock" }};
      if (v === false) return {{ cls: "s-out", label: "Out of Stock" }};
      return {{ cls: "s-unk", label: "Unknown" }};
    }}

    function availRank(v) {{
      return v === true ? 0 : (v === false ? 1 : 2);
    }}

    function cmp(a, b) {{
      if (sortCol === "available") return (availRank(a.available) - availRank(b.available)) * sortDir;
      if (sortCol === "price_value") {{
        const av = (typeof a.price_value === "number") ? a.price_value : Number.POSITIVE_INFINITY;
        const bv = (typeof b.price_value === "number") ? b.price_value : Number.POSITIVE_INFINITY;
        return (av - bv) * sortDir;
      }}
      return String(a[sortCol] ?? "").localeCompare(String(b[sortCol] ?? ""), undefined, {{ numeric: true, sensitivity: "base" }}) * sortDir;
    }}

    function esc(s) {{
      return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }}

    function recalcCounters(items) {{
      const ok = items.filter(p => p.available === true).length;
      const bad = items.filter(p => p.available === false).length;
      const unk = items.length - ok - bad;
      document.getElementById("cOk").textContent = ok;
      document.getElementById("cBad").textContent = bad;
      document.getElementById("cUnk").textContent = unk;
      document.getElementById("cTot").textContent = items.length;
    }}

    function getFilteredSortedItems() {{
      const needle = (q.value || "").trim().toLowerCase();
      const siteV = site.value || "";
      const stockV = stockFilter.value || "";
      const minV = minPrice.value === "" ? null : Number(minPrice.value);
      const maxV = maxPrice.value === "" ? null : Number(maxPrice.value);
      const specialV = specialFilter.value || "all";

      return DATA.products.filter(p => {{
        if (siteV && p.domain !== siteV) return false;
        if (stockV === "in" && p.available !== true) return false;
        if (stockV === "out" && p.available !== false) return false;
        if (stockV === "unknown" && p.available != null) return false;
        if (specialV === "only" && !p.is_special) return false;
        if (specialV === "exclude" && p.is_special) return false;
        if (needle && !p._blob.includes(needle)) return false;
        if (minV != null && !(typeof p.price_value === "number" && p.price_value >= minV)) return false;
        if (maxV != null && !(typeof p.price_value === "number" && p.price_value <= maxV)) return false;
        return true;
      }}).sort(cmp);
    }}

    function render() {{
      const items = getFilteredSortedItems();
      recalcCounters(items);

      const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
      if (page > totalPages) page = totalPages;
      if (page < 1) page = 1;
      const start = (page - 1) * PAGE_SIZE;
      const end = Math.min(items.length, start + PAGE_SIZE);
      const visible = items.slice(start, end);

      resultCount.textContent = `${{items.length}} products | Click headers to sort.`;
      pageMeta.textContent = `Page ${{page}} / ${{totalPages}} (${{start + 1}}-${{end}})`;
      prevPage.disabled = page <= 1;
      nextPage.disabled = page >= totalPages;

      table.querySelectorAll("thead th[data-col]").forEach(th => {{
        const col = th.getAttribute("data-col");
        const text = th.textContent.replace(/\\s*[▲▼]$/, "");
        th.textContent = (col === sortCol) ? `${{text}} ${{sortDir === 1 ? "▲" : "▼"}}` : text;
      }});

      const frag = document.createDocumentFragment();
      if (!visible.length) {{
        const tr = document.createElement("tr");
        tr.innerHTML = '<td colspan="7"><div class="empty">No products match your filters</div></td>';
        frag.appendChild(tr);
      }} else {{
        for (const p of visible) {{
          const m = statusMeta(p.available);
          const tr = document.createElement("tr");
          const specs = Object.entries(p.specs || {{}})
            .filter(([k,_]) => String(k).trim().toLowerCase() !== "cycles")
            .slice(0, 10)
            .map(([k,v]) => `<span class="chip">${{esc(k)}}: ${{esc(v)}}</span>`)
            .join("");
          const locs = Array.isArray(p.locations) && p.locations.length ? p.locations : (p.location ? [p.location] : []);
          const locTags = locs.slice(0, 3).map(v => `<span class="tag">${{esc(v)}}</span>`).join("") + (locs.length > 3 ? `<span class="tag">+${{locs.length - 3}} more</span>` : "");
          const specialTag = p.is_special ? '<span class="tag tag-special">Special</span>' : "";
          const cycles = Array.isArray(p.billing_cycles) ? p.billing_cycles.join(", ") : "";
          const cp = Object.entries(p.cycle_prices || {{}}).map(([k,v]) => `<div>${{esc(k)}}: ${{esc(v)}}</div>`).join("");
          const priceBlock = p.price ? `<div><b>${{esc(p.price)}}</b></div>${{cp ? `<div class="cycles">${{cp}}</div>` : ""}}` : '<span class="cycles">-</span>';

          const descBlock = p.original_description ? `<div><i>${{esc(p.original_description.substring(0, 150))}}${{p.original_description.length > 150 ? '...' : ''}}</i></div>` : (p.description ? `<div><i>${{esc(p.description.substring(0, 150))}}${{p.description.length > 150 ? '...' : ''}}</i></div>` : "");

          tr.innerHTML = `
            <td data-k="Status"><span class="status ${{m.cls}}">${{m.label}}</span></td>
            <td data-k="Domain"><span class="domain">${{esc(p.domain)}}</span></td>
            <td data-k="Product" class="name">
              <div><a href="${{esc(p.url)}}" target="_blank" rel="noreferrer noopener">${{esc(p.original_name ? p.original_name : p.name)}}</a>${{locTags}}${{specialTag}}</div>
              ${{p.original_name && p.original_name !== p.name ? `<div class="cycles">Cleaned Name: ${{esc(p.name)}}</div>` : ""}}
              ${{p.variant_of ? `<div class="cycles">Plan: ${{esc(p.variant_of)}}</div>` : ""}}
              ${{descBlock}}
              <div style="margin-top: 4px;">${{specs}}</div>
            </td>
            <td data-k="Price">${{priceBlock}}</td>
            <td data-k="Cycles"><span class="cycles">${{esc(cycles || "-")}}</span></td>
            <td data-k="Last Seen"><span class="cycles">${{esc(p.last_seen || "")}}</span></td>
            <td data-k="Action"><a class="btn" href="${{esc(p.url)}}" target="_blank" rel="noreferrer noopener">Buy Now</a></td>
          `;
          frag.appendChild(tr);
        }}
      }}

      tb.innerHTML = "";
      tb.appendChild(frag);
    }}

    [q, site, stockFilter, minPrice, maxPrice, specialFilter].forEach(el => {{
      el.addEventListener("input", () => {{ page = 1; render(); }});
      el.addEventListener("change", () => {{ page = 1; render(); }});
    }});

    prevPage.addEventListener("click", () => {{ if (page > 1) {{ page -= 1; render(); }} }});
    nextPage.addEventListener("click", () => {{ page += 1; render(); }});

    table.querySelectorAll("thead th[data-col]").forEach(th => {{
      th.addEventListener("click", () => {{
        const col = th.getAttribute("data-col");
        if (!col) return;
        if (sortCol === col) sortDir *= -1;
        else {{ sortCol = col; sortDir = 1; }}
        render();
      }});
    }});

    const domains = Array.from(new Set(DATA.products.map(p => p.domain).filter(Boolean))).sort();
    for (const d of domains) {{
      const o = document.createElement("option");
      o.value = d;
      o.textContent = d;
      site.appendChild(o);
    }}

    render();
  </script>
</body>
</html>
"""
