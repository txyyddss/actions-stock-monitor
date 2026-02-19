from __future__ import annotations

import html
import json
import os
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
    """Return a compact timestamp string suitable for small screens."""
    dt = _parse_iso(ts)
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def render_dashboard_html(state: dict[str, Any], *, run_summary: dict[str, Any] | None = None) -> str:
    run_summary = run_summary or {}
    updated_at = state.get("updated_at") or run_summary.get("finished_at") or ""
    stale_minutes = int(os.getenv("STALE_MINUTES", "180"))

    # Use real current UTC time for stale detection, not the potentially-old updated_at.
    now = datetime.now(timezone.utc)

    products: list[dict[str, Any]] = []
    for _, p in (state.get("products") or {}).items():
        if not isinstance(p, dict):
            continue
        last_seen_dt = _parse_iso(p.get("last_seen"))
        stale = False
        if last_seen_dt:
            age_min = int((now - last_seen_dt).total_seconds() / 60)
            stale = age_min >= stale_minutes
        else:
            # If we have no last_seen at all, consider it stale only if > stale_minutes old.
            first_seen_dt = _parse_iso(p.get("first_seen"))
            if first_seen_dt:
                stale = int((now - first_seen_dt).total_seconds() / 60) >= stale_minutes

        billing = p.get("billing_cycles") or []
        if isinstance(billing, list):
            billing = ", ".join(str(c) for c in billing)
        else:
            billing = str(billing) if billing else ""

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
                "stale": stale,
                "billing_cycles": billing,
                "option": p.get("option") or "",
                "variant_of": p.get("variant_of") or "",
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
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet" />
  <style>
    :root {{
      --bg0: #070914;
      --bg1: #0b0f23;
      --panel: rgba(255, 255, 255, 0.06);
      --border: rgba(255, 255, 255, 0.12);
      --text: rgba(255, 255, 255, 0.92);
      --muted: rgba(255, 255, 255, 0.62);
      --cyan: #00f0ff;
      --magenta: #ff2bd6;
      --lime: #7CFF00;
      --red: #ff4d4d;
      --amber: #ffbf00;
      --shadow: 0 12px 48px rgba(0,0,0,.55);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      background:
        radial-gradient(1200px 800px at 10% 10%, rgba(0, 240, 255, 0.16), transparent 55%),
        radial-gradient(900px 700px at 85% 20%, rgba(255, 43, 214, 0.14), transparent 55%),
        radial-gradient(900px 700px at 35% 90%, rgba(124, 255, 0, 0.10), transparent 55%),
        linear-gradient(180deg, var(--bg0), var(--bg1));
      font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji";
    }}
    a {{ color: var(--cyan); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .plink {{ color: var(--text); }}
    .plink:hover {{ color: var(--cyan); }}
    .wrap {{ max-width: 1280px; margin: 0 auto; padding: 24px 16px 48px; }}
    header {{
      display: flex; gap: 16px; align-items: flex-end; justify-content: space-between;
      padding: 18px 18px 16px;
      border: 1px solid var(--border);
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.03));
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
      flex-wrap: wrap;
    }}
    .title {{
      font-weight: 800;
      letter-spacing: .4px;
      font-size: 20px;
      margin: 0;
    }}
    .sub {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.35;
    }}
    .stats {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: end;
    }}
    .pill {{
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 8px 10px;
      background: rgba(255,255,255,.04);
      font-size: 12px;
      color: var(--muted);
      white-space: nowrap;
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .pill b {{ color: var(--text); }}
    .pill.run-pill {{
      white-space: normal;
      word-break: break-all;
      font-size: 11px;
    }}
    .controls {{
      margin: 16px 0 10px;
      display: flex; gap: 12px; flex-wrap: wrap;
      align-items: center;
    }}
    input[type="search"] {{
      width: min(520px, 100%);
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(0,0,0,.25);
      color: var(--text);
      outline: none;
      font-family: inherit;
    }}
    input[type="search"]:focus {{
      border-color: rgba(0, 240, 255, 0.55);
      box-shadow: 0 0 0 3px rgba(0, 240, 255, 0.12);
    }}
    select {{
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(0,0,0,.25);
      color: var(--text);
      outline: none;
      font-family: inherit;
    }}
    select:focus {{
      border-color: rgba(0, 240, 255, 0.55);
      box-shadow: 0 0 0 3px rgba(0, 240, 255, 0.12);
    }}
    .table-wrap {{
      overflow-x: auto;
      border-radius: 16px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,.03);
      box-shadow: var(--shadow);
    }}
    .table {{
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      overflow: hidden;
    }}
    thead th {{
      position: sticky; top: 0;
      background: rgba(10, 12, 26, 0.96);
      backdrop-filter: blur(8px);
      text-align: left;
      font-size: 12px;
      color: var(--muted);
      padding: 12px 12px;
      border-bottom: 1px solid var(--border);
      user-select: none;
      cursor: pointer;
      white-space: nowrap;
    }}
    thead th:hover {{
      color: var(--cyan);
    }}
    thead th.sorted {{
      color: var(--cyan);
    }}
    tbody td {{
      padding: 12px 12px;
      border-bottom: 1px solid rgba(255,255,255,.08);
      vertical-align: top;
      font-size: 13px;
    }}
    tbody tr:hover td {{
      background: rgba(0, 240, 255, 0.05);
    }}
    .muted {{ color: var(--muted); }}
    .badge {{
      display: inline-flex; align-items: center; gap: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,.14);
      background: rgba(255,255,255,.03);
      font-size: 12px;
      color: var(--muted);
    }}
    .dot {{
      width: 10px; height: 10px; border-radius: 50%;
      box-shadow: 0 0 12px rgba(0,0,0,.25);
      flex-shrink: 0;
    }}
    .dot.ok {{ background: var(--lime); box-shadow: 0 0 18px rgba(124,255,0,.35); }}
    .dot.bad {{ background: var(--red); box-shadow: 0 0 18px rgba(255,77,77,.25); }}
    .dot.unk {{ background: var(--amber); box-shadow: 0 0 18px rgba(255,191,0,.22); }}
    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 8px 10px;
      border-radius: 12px;
      background: linear-gradient(180deg, rgba(0,240,255,.18), rgba(0,240,255,.07));
      border: 1px solid rgba(0,240,255,.35);
      color: var(--text);
      font-weight: 650;
      font-size: 12px;
      transition: all 0.2s ease;
    }}
    .btn:hover {{
      border-color: rgba(0,240,255,.65);
      box-shadow: 0 0 0 3px rgba(0,240,255,.12);
      text-decoration: none;
      transform: translateY(-1px);
    }}
    .specs {{
      margin-top: 6px;
      display: flex; flex-wrap: wrap; gap: 6px;
    }}
    .desc {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.35;
    }}
    .chip {{
      padding: 4px 8px;
      border-radius: 999px;
      background: rgba(255,255,255,.04);
      border: 1px solid rgba(255,255,255,.10);
      color: var(--muted);
      font-size: 11px;
    }}
    .tag-stale {{
      margin-left: 8px;
      padding: 2px 6px;
      border-radius: 8px;
      font-size: 11px;
      color: rgba(0,0,0,.85);
      background: var(--amber);
    }}
    .option-tag {{
      margin-left: 6px;
      padding: 2px 6px;
      border-radius: 8px;
      font-size: 11px;
      color: var(--cyan);
      border: 1px solid rgba(0,240,255,.3);
      background: rgba(0,240,255,.08);
    }}
    .viz {{
      display: flex;
      gap: 14px;
      align-items: center;
      flex-wrap: wrap;
      margin: 12px 0 14px;
      padding: 12px;
      border: 1px solid var(--border);
      border-radius: 14px;
      background: rgba(255,255,255,.03);
      box-shadow: 0 10px 36px rgba(0,0,0,.35);
    }}
    .donut {{
      width: 92px;
      height: 92px;
      border-radius: 50%;
      /* Start with neutral; JS will set correct conic-gradient */
      background: rgba(255,255,255,.18);
      position: relative;
      border: 1px solid var(--border);
      transition: background 0.3s ease;
    }}
    .donut::after {{
      content: "";
      position: absolute;
      inset: 20px;
      border-radius: 50%;
      background: rgba(0,0,0,.38);
      border: 1px solid rgba(255,255,255,.10);
    }}
    .legend {{
      display: flex;
      gap: 10px 14px;
      flex-wrap: wrap;
      font-size: 12px;
      color: var(--muted);
      align-items: center;
    }}
    .litem {{ display: inline-flex; align-items: center; gap: 7px; }}
    .swatch {{
      width: 10px;
      height: 10px;
      border-radius: 3px;
      display: inline-block;
    }}
    .sw-ok {{ background: var(--lime); }}
    .sw-bad {{ background: var(--red); }}
    .sw-unk {{ background: rgba(255,255,255,.18); }}
    @media (max-width: 760px) {{
      header {{ flex-direction: column; align-items: flex-start; }}
      .stats {{ width: 100%; }}
      .pill.run-pill {{ display: none; }}
      thead {{ display: none; }}
      .table-wrap {{ border-radius: 12px; }}
      .table, tbody, tr, td {{ display: block; width: 100%; }}
      tbody td {{
        border-bottom: none;
        padding: 10px 12px;
      }}
      tbody tr {{
        border-bottom: 1px solid rgba(255,255,255,.10);
      }}
      tbody td[data-k]::before {{
        content: attr(data-k);
        display: block;
        font-size: 11px;
        color: var(--muted);
        margin-bottom: 4px;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div>
        <h1 class="title">Restock Monitor — Cyber Dashboard</h1>
        <div class="sub">
          Last updated: <b>{_h(updated_at)}</b><br/>
          Domains: <b>{domains_ok}</b> ok, <b>{domains_error}</b> error · Products: <b>{len(products)}</b>
        </div>
      </div>
      <div class="stats">
        <div class="pill"><a href="https://t.me/tx_stock_monitor" target="_blank" rel="noreferrer noopener">Telegram group</a></div>
        <div class="pill">Restocks: <b>{_h(run_summary.get("restocks", 0))}</b></div>
        <div class="pill">New: <b>{_h(run_summary.get("new_products", 0))}</b></div>
        <div class="pill run-pill">Run: <span class="muted">{_h(run_started)}</span> → <span class="muted">{_h(run_finished)}</span></div>
      </div>
    </header>

    <div class="controls">
      <input id="q" type="search" placeholder="Search domain, name, price, specs…" autocomplete="off" />
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
        <span class="litem"><span class="swatch sw-ok"></span> In Stock: <b id="cOk">0</b></span>
        <span class="litem"><span class="swatch sw-bad"></span> Out: <b id="cBad">0</b></span>
        <span class="litem"><span class="swatch sw-unk"></span> Unknown: <b id="cUnk">0</b></span>
        <span class="litem">Total: <b id="cTot">0</b></span>
      </div>
    </div>

    <div class="table-wrap">
    <table class="table" id="t">
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

  <script>
    const DATA = {data_json};
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
      return String(av ?? "").localeCompare(String(bv ?? ""), undefined, {{numeric:true, sensitivity:"base"}}) * sortDir;
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
        pie.style.background = "rgba(255,255,255,.18)";
        return;
      }}
      const okPct = (ok / total) * 100;
      const badPct = (bad / total) * 100;
      const a = okPct;
      const b = okPct + badPct;
      pie.style.background = `conic-gradient(var(--lime) 0% ${{a}}%, var(--red) ${{a}}% ${{b}}%, rgba(255,255,255,.18) ${{b}}% 100%)`;
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
          const blob = `${{p.domain}} ${{p.name}} ${{p.price}} ${{p.description || ""}} ${{specText}} ${{p.url}} ${{p.billing_cycles || ""}} ${{p.option || ""}}`.toLowerCase();
          return blob.includes(needle);
        }})
        .slice()
        .sort(cmp);

      updatePie(items);

      // Update sort indicator
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
        const desc = p.description ? `<div class="desc">${{escapeHtml(p.description)}}</div>` : "";
        const staleTag = p.stale ? `<span class="tag-stale">STALE</span>` : "";
        const optionTag = p.option ? `<span class="option-tag">${{escapeHtml(p.option)}}</span>` : "";
        const variantInfo = p.variant_of ? `<div class="muted" style="font-size:11px;margin-top:2px">Plan: ${{escapeHtml(p.variant_of)}}</div>` : "";
        const cyclesCell = p.billing_cycles ? escapeHtml(p.billing_cycles) : '<span class="muted">—</span>';

        tr.innerHTML = `
          <td data-k="Status">
            <span class="badge"><span class="dot ${{meta.cls}}"></span> ${{meta.label}} ${{staleTag}}</span>
          </td>
          <td data-k="Domain"><span class="muted">${{escapeHtml(p.domain)}}</span></td>
          <td data-k="Product">
            <div><a class="plink" href="${{escapeHtml(p.url)}}" target="_blank" rel="noreferrer noopener"><b>${{escapeHtml(p.name)}}</b></a>${{optionTag}}</div>
            ${{variantInfo}}
            ${{desc}}
            <div class="specs">${{specs}}</div>
          </td>
          <td data-k="Price">${{escapeHtml(p.price || "")}}</td>
          <td data-k="Cycles">${{cyclesCell}}</td>
          <td data-k="Last Seen"><span class="muted">${{escapeHtml(p.last_seen || "")}}</span></td>
          <td data-k="Action"><a class="btn" href="${{escapeHtml(p.url)}}" target="_blank" rel="noreferrer noopener">Buy Now</a></td>
        `;
        tb.appendChild(tr);
      }}
    }}

    function escapeHtml(s) {{
      return String(s ?? "").replace(/[&<>"]/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\\\"":"&quot;"}}[c]));
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

    // Populate site/category filter.
    if (site) {{
      const domains = Array.from(new Set((DATA.products || []).map(p => String(p.domain || \"\")).filter(Boolean))).sort();
      for (const d of domains) {{
        const opt = document.createElement(\"option\");
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
