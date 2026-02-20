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
                "location": p.get("location") or p.get("option") or "",
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
  <title>Restock Monitor Dashboard</title>
  <meta name="description" content="Real-time VPS hosting stock monitor dashboard tracking product availability across multiple providers." />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet" />
  <style>
    :root {{
      --bg: #061426;
      --bg2: #0a1f35;
      --panel: rgba(255, 255, 255, 0.06);
      --line: rgba(255, 255, 255, 0.14);
      --txt: #f2fbff;
      --muted: rgba(242, 251, 255, 0.68);
      --ok: #2ed37a;
      --bad: #ff6565;
      --unk: #ffc55a;
      --accent: #42e5ff;
      --accent2: #6fffcf;
      --special: #ffdb6e;
      --shadow: 0 12px 36px rgba(0,0,0,.35);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--txt);
      background:
        radial-gradient(900px 540px at 8% 0%, rgba(66,229,255,.22), transparent 60%),
        radial-gradient(860px 520px at 92% 0%, rgba(111,255,207,.16), transparent 60%),
        linear-gradient(180deg, var(--bg), var(--bg2));
      font-family: "Space Grotesk", sans-serif;
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .wrap {{ max-width: 1320px; margin: 0 auto; padding: 22px 14px 40px; }}
    header {{
      border: 1px solid var(--line);
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255,255,255,.1), rgba(255,255,255,.03));
      box-shadow: var(--shadow);
      padding: 14px 16px;
      display: flex;
      gap: 12px;
      justify-content: space-between;
      align-items: flex-end;
      flex-wrap: wrap;
    }}
    .title {{ margin: 0; font-weight: 700; letter-spacing: .2px; }}
    .sub {{ margin-top: 4px; color: var(--muted); font-size: 13px; line-height: 1.35; }}
    .stats {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .pill {{
      border: 1px solid var(--line);
      background: rgba(255,255,255,.04);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
    }}
    .pill b {{ color: var(--txt); }}
    .controls {{
      margin: 12px 0 10px;
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }}
    input[type="search"], select {{
      border-radius: 12px;
      border: 1px solid var(--line);
      background: rgba(0,0,0,.2);
      color: var(--txt);
      padding: 10px 12px;
      font-family: inherit;
      outline: none;
    }}
    input[type="search"] {{ width: min(540px, 100%); }}
    .viz {{
      margin: 10px 0 12px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(255,255,255,.03);
      padding: 10px 12px;
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
    }}
    .donut {{
      width: 86px;
      height: 86px;
      border-radius: 50%;
      border: 1px solid var(--line);
      background: rgba(255,255,255,.16);
      position: relative;
    }}
    .donut::after {{
      content: "";
      position: absolute;
      inset: 18px;
      border-radius: 50%;
      background: rgba(0,0,0,.36);
      border: 1px solid rgba(255,255,255,.12);
    }}
    .legend {{ display: flex; gap: 8px 12px; flex-wrap: wrap; color: var(--muted); font-size: 12px; }}
    .sw {{ width: 10px; height: 10px; border-radius: 3px; display: inline-block; }}
    .sw-ok {{ background: var(--ok); }}
    .sw-bad {{ background: var(--bad); }}
    .sw-unk {{ background: rgba(255,255,255,.18); }}
    .table-wrap {{
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255,255,255,.03);
      overflow: auto;
      box-shadow: var(--shadow);
    }}
    table {{ width: 100%; border-collapse: collapse; }}
    thead th {{
      position: sticky; top: 0;
      background: rgba(6,20,38,.95);
      border-bottom: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      padding: 10px;
      text-align: left;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }}
    thead th.sorted {{ color: var(--accent); }}
    tbody td {{
      border-bottom: 1px solid rgba(255,255,255,.1);
      padding: 10px;
      font-size: 13px;
      vertical-align: top;
    }}
    tbody tr:hover td {{ background: rgba(66,229,255,.06); }}
    .muted {{ color: var(--muted); }}
    .badge {{
      display: inline-flex;
      gap: 8px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 8px;
      color: var(--muted);
      font-size: 12px;
    }}
    .dot {{ width: 9px; height: 9px; border-radius: 50%; display: inline-block; }}
    .ok {{ background: var(--ok); }}
    .bad {{ background: var(--bad); }}
    .unk {{ background: var(--unk); }}
    .chip {{
      display: inline-block;
      border: 1px solid rgba(255,255,255,.12);
      border-radius: 999px;
      background: rgba(255,255,255,.05);
      padding: 3px 7px;
      font-size: 11px;
      color: var(--muted);
      margin: 2px 4px 0 0;
    }}
    .location-tag {{
      margin-left: 6px;
      padding: 2px 6px;
      border-radius: 8px;
      border: 1px solid rgba(66,229,255,.34);
      background: rgba(66,229,255,.12);
      color: var(--accent);
      font-size: 11px;
    }}
    .special-tag {{
      margin-left: 6px;
      padding: 2px 6px;
      border-radius: 8px;
      border: 1px solid rgba(255,219,110,.45);
      background: rgba(255,219,110,.16);
      color: var(--special);
      font-size: 11px;
    }}
    .desc-wrap {{
      margin-top: 6px;
      border: 1px solid rgba(255,255,255,.12);
      border-radius: 8px;
      overflow: hidden;
      background: rgba(0,0,0,.22);
    }}
    .desc-wrap summary {{ padding: 6px 8px; font-size: 11px; color: var(--muted); cursor: pointer; }}
    .desc-box {{ padding: 7px 8px; font-size: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }}
    .btn {{
      display: inline-flex;
      border-radius: 10px;
      border: 1px solid rgba(66,229,255,.4);
      background: rgba(66,229,255,.14);
      color: var(--txt);
      font-weight: 700;
      padding: 7px 10px;
      font-size: 12px;
      text-decoration: none;
    }}
    .btn:hover {{ text-decoration: none; border-color: rgba(66,229,255,.7); }}
    @media (max-width: 760px) {{
      thead {{ display: none; }}
      table, tbody, tr, td {{ display: block; width: 100%; }}
      tbody td[data-k]::before {{
        content: attr(data-k);
        display: block;
        color: var(--muted);
        font-size: 11px;
        margin-bottom: 4px;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1 class="title">Restock Monitor - Cyber Dashboard</h1>
        <div class="sub">
          Last updated: <b>{_h(updated_at)}</b><br/>
          Domains: <b>{domains_ok}</b> ok, <b>{domains_error}</b> error | Products: <b>{len(products)}</b>
        </div>
      </div>
      <div class="stats">
        <div class="pill"><a href="https://t.me/tx_stock_monitor" target="_blank" rel="noreferrer noopener">Telegram group</a></div>
        <div class="pill">Restocks: <b>{_h(run_summary.get("restocks", 0))}</b></div>
        <div class="pill">New: <b>{_h(run_summary.get("new_products", 0))}</b></div>
        <div class="pill">Run: <span class="muted">{_h(run_started)}</span> -> <span class="muted">{_h(run_finished)}</span></div>
      </div>
    </header>

    <div class="controls">
      <input id="q" type="search" placeholder="Search domain, name, price, specs, details" autocomplete="off" />
      <select id="site" aria-label="Site category">
        <option value="">All sites</option>
      </select>
      <select id="stock-filter" aria-label="Stock filter">
        <option value="">All stock</option>
        <option value="in">In Stock</option>
        <option value="out">Out of Stock</option>
        <option value="unknown">Unknown</option>
      </select>
      <span class="muted">Click headers to sort.</span>
    </div>

    <div class="viz" aria-label="Stock distribution chart">
      <div class="donut" id="pie" title="In Stock / Out of Stock / Unknown"></div>
      <div class="legend">
        <span><span class="sw sw-ok"></span> In Stock: <b id="cOk">0</b></span>
        <span><span class="sw sw-bad"></span> Out: <b id="cBad">0</b></span>
        <span><span class="sw sw-unk"></span> Unknown: <b id="cUnk">0</b></span>
        <span>Total: <b id="cTot">0</b></span>
      </div>
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
  </div>

  <script id="dashboard-data" type="application/json">{data_json_safe}</script>
  <script>
    let DATA = {{ products: [] }};
    try {{
      const dataNode = document.getElementById("dashboard-data");
      DATA = JSON.parse((dataNode && dataNode.textContent) ? dataNode.textContent : '{{"products":[]}}');
    }} catch (_err) {{
      DATA = {{ products: [] }};
    }}

    const tb = document.getElementById("tb");
    const q = document.getElementById("q");
    const site = document.getElementById("site");
    const stockFilter = document.getElementById("stock-filter");
    const table = document.getElementById("t");
    const pie = document.getElementById("pie");
    const cOk = document.getElementById("cOk");
    const cBad = document.getElementById("cBad");
    const cUnk = document.getElementById("cUnk");
    const cTot = document.getElementById("cTot");

    let sortCol = "available";
    let sortDir = 1;

    function statusMeta(avail) {{
      if (avail === true) return {{ cls: "ok", label: "In Stock" }};
      if (avail === false) return {{ cls: "bad", label: "Out of Stock" }};
      return {{ cls: "unk", label: "Unknown" }};
    }}

    function cmp(a, b) {{
      const av = a[sortCol];
      const bv = b[sortCol];
      if (sortCol === "available") {{
        const rank = (v) => (v === true ? 0 : (v === false ? 1 : 2));
        return (rank(av) - rank(bv)) * sortDir;
      }}
      return String(av ?? "").localeCompare(String(bv ?? ""), undefined, {{ numeric: true, sensitivity: "base" }}) * sortDir;
    }}

    function updatePie(items) {{
      const total = items.length || 0;
      const ok = items.filter(p => p.available === true).length;
      const bad = items.filter(p => p.available === false).length;
      const unk = total - ok - bad;
      if (cOk) cOk.textContent = String(ok);
      if (cBad) cBad.textContent = String(bad);
      if (cUnk) cUnk.textContent = String(unk);
      if (cTot) cTot.textContent = String(total);
      if (!pie) return;
      if (total === 0) {{
        pie.style.background = "rgba(255,255,255,.16)";
        return;
      }}
      const a = (ok / total) * 100;
      const b = a + (bad / total) * 100;
      pie.style.background = `conic-gradient(var(--ok) 0% ${{a}}%, var(--bad) ${{a}}% ${{b}}%, rgba(255,255,255,.16) ${{b}}% 100%)`;
    }}

    function escapeHtml(s) {{
      return String(s ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }}

    function render() {{
      const needle = (q.value || "").trim().toLowerCase();
      const siteNeedle = (site && site.value) ? String(site.value) : "";
      const stockNeedle = (stockFilter && stockFilter.value) ? String(stockFilter.value) : "";
      const items = DATA.products
        .filter(p => {{
          if (siteNeedle && String(p.domain || "") !== siteNeedle) return false;
          if (stockNeedle === "in" && p.available !== true) return false;
          if (stockNeedle === "out" && p.available !== false) return false;
          if (stockNeedle === "unknown" && p.available !== null) return false;
          if (!needle) return true;
          const specText = Object.entries(p.specs || {{}}).map(([k,v]) => `${{k}}:${{v}}`).join(" ");
          const cpText = Object.entries(p.cycle_prices || {{}}).map(([k,v]) => `${{k}}:${{v}}`).join(" ");
          const blob = `${{p.domain}} ${{p.name}} ${{p.price}} ${{p.description || ""}} ${{specText}} ${{p.url}} ${{p.billing_cycles || ""}} ${{p.location || ""}} ${{cpText}}`.toLowerCase();
          return blob.includes(needle);
        }})
        .slice()
        .sort(cmp);

      updatePie(items);

      table.querySelectorAll("thead th[data-col]").forEach(th => {{
        th.classList.toggle("sorted", th.getAttribute("data-col") === sortCol);
        th.textContent = th.textContent.replace(/ [▲▼]$/, "");
        if (th.getAttribute("data-col") === sortCol) {{
          th.textContent += sortDir === 1 ? " ▲" : " ▼";
        }}
      }});

      tb.innerHTML = "";
      for (const p of items) {{
        const meta = statusMeta(p.available);
        const tr = document.createElement("tr");
        const specs = Object.entries(p.specs || {{}}).map(([k,v]) => `<span class="chip">${{escapeHtml(k)}}: ${{escapeHtml(v)}}</span>`).join("");
        const desc = p.description
          ? `<details class="desc-wrap"><summary>Description</summary><div class="desc-box">${{escapeHtml(p.description)}}</div></details>`
          : "";
        const locationTag = p.location ? `<span class="location-tag">${{escapeHtml(p.location)}}</span>` : "";
        const specialTag = p.is_special ? `<span class="special-tag">Special</span>` : "";
        const variantInfo = p.variant_of ? `<div class="muted" style="font-size:11px;margin-top:2px">Plan: ${{escapeHtml(p.variant_of)}}</div>` : "";
        const cyclesCell = p.billing_cycles ? escapeHtml(p.billing_cycles) : '<span class="muted">—</span>';
        const cyclePrices = Object.entries(p.cycle_prices || {{}}).map(([k,v]) => `<div><span class="muted">${{escapeHtml(k)}}:</span> ${{escapeHtml(v)}}</div>`).join("");
        const priceCell = p.price
          ? `<div>${{escapeHtml(p.price)}}</div>${{cyclePrices ? `<div class="muted" style="font-size:11px; margin-top:4px">${{cyclePrices}}</div>` : ""}}`
          : `<span class="muted">—</span>`;

        tr.innerHTML = `
          <td data-k="Status"><span class="badge"><span class="dot ${{meta.cls}}"></span> ${{meta.label}}</span></td>
          <td data-k="Domain"><span class="muted">${{escapeHtml(p.domain)}}</span></td>
          <td data-k="Product">
            <div><a class="plink" href="${{escapeHtml(p.url)}}" target="_blank" rel="noreferrer noopener"><b>${{escapeHtml(p.name)}}</b></a>${{locationTag}}${{specialTag}}</div>
            ${{variantInfo}}
            ${{desc}}
            <div>${{specs}}</div>
          </td>
          <td data-k="Price">${{priceCell}}</td>
          <td data-k="Cycles">${{cyclesCell}}</td>
          <td data-k="Last Seen"><span class="muted">${{escapeHtml(p.last_seen || "")}}</span></td>
          <td data-k="Action"><a class="btn" href="${{escapeHtml(p.url)}}" target="_blank" rel="noreferrer noopener">Buy Now</a></td>
        `;
        tb.appendChild(tr);
      }}
    }}

    q.addEventListener("input", () => render());
    if (site) site.addEventListener("change", () => render());
    if (stockFilter) stockFilter.addEventListener("change", () => render());

    table.querySelectorAll("thead th[data-col]").forEach(th => {{
      th.addEventListener("click", () => {{
        const col = th.getAttribute("data-col");
        if (!col) return;
        if (sortCol === col) sortDir *= -1;
        else {{ sortCol = col; sortDir = 1; }}
        render();
      }});
    }});

    if (site) {{
      const domains = Array.from(new Set((DATA.products || []).map(p => String(p.domain || "")).filter(Boolean))).sort();
      for (const d of domains) {{
        const opt = document.createElement("option");
        opt.value = d;
        opt.textContent = d;
        site.appendChild(opt);
      }}
    }}

    render();
  </script>
</body>
</html>
"""
