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


def render_dashboard_html(state: dict[str, Any], *, run_summary: dict[str, Any] | None = None) -> str:
    run_summary = run_summary or {}
    updated_at = state.get("updated_at") or run_summary.get("finished_at") or ""
    stale_minutes = int(os.getenv("STALE_MINUTES", "180"))

    now = _parse_iso(updated_at) or datetime.now(timezone.utc)

    products: list[dict[str, Any]] = []
    for _, p in (state.get("products") or {}).items():
        if not isinstance(p, dict):
            continue
        last_seen_dt = _parse_iso(p.get("last_seen"))
        stale = False
        if last_seen_dt:
            age_min = int((now - last_seen_dt).total_seconds() / 60)
            stale = age_min >= stale_minutes
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

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Restock Monitor Dashboard</title>
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
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji";
    }}
    a {{ color: var(--cyan); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .plink {{ color: var(--text); }}
    .plink:hover {{ color: var(--cyan); }}
    .wrap {{ max-width: 1200px; margin: 0 auto; padding: 24px 16px 48px; }}
    header {{
      display: flex; gap: 16px; align-items: flex-end; justify-content: space-between;
      padding: 18px 18px 16px;
      border: 1px solid var(--border);
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255,255,255,.07), rgba(255,255,255,.03));
      box-shadow: var(--shadow);
      backdrop-filter: blur(10px);
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
      display: grid;
      grid-auto-flow: column;
      grid-gap: 10px;
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
    }}
    .pill b {{ color: var(--text); }}
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
    }}
    select:focus {{
      border-color: rgba(0, 240, 255, 0.55);
      box-shadow: 0 0 0 3px rgba(0, 240, 255, 0.12);
    }}
    .table {{
      width: 100%;
      border-collapse: separate;
      border-spacing: 0;
      overflow: hidden;
      border-radius: 16px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,.03);
      box-shadow: var(--shadow);
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
    }}
    .btn:hover {{
      border-color: rgba(0,240,255,.65);
      box-shadow: 0 0 0 3px rgba(0,240,255,.12);
      text-decoration: none;
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
      background: conic-gradient(var(--lime) 0 33%, var(--red) 33% 66%, rgba(255,255,255,.18) 66% 100%);
      position: relative;
      border: 1px solid var(--border);
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
      .stats {{ grid-auto-flow: row; width: 100%; }}
      thead {{ display: none; }}
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
        <div class="pill">Run: <span class="muted">{_h(run_summary.get("started_at",""))}</span> → <span class="muted">{_h(run_summary.get("finished_at",""))}</span></div>
      </div>
    </header>

    <div class="controls">
      <input id="q" type="search" placeholder="Search domain, name, price, specs…" autocomplete="off" />
      <select id="site" aria-label="Site category">
        <option value="">All sites</option>
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

    <table class="table" id="t">
      <thead>
        <tr>
          <th data-col="available">Status</th>
          <th data-col="domain">Domain</th>
          <th data-col="name">Product</th>
          <th data-col="price">Price</th>
          <th data-col="last_seen">Last Seen</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody id="tb"></tbody>
    </table>
  </div>

  <script>
    const DATA = {data_json};
    const tb = document.getElementById("tb");
    const q = document.getElementById("q");
    const site = document.getElementById("site");
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
      const okPct = total ? (ok / total) * 100 : 0;
      const badPct = total ? (bad / total) * 100 : 0;
      const a = okPct;
      const b = okPct + badPct;
      pie.style.background = `conic-gradient(var(--lime) 0% ${{a}}%, var(--red) ${{a}}% ${{b}}%, rgba(255,255,255,.18) ${{b}}% 100%)`;
    }}

    function render() {{
      const needle = (q.value || "").trim().toLowerCase();
      const siteNeedle = (site && site.value) ? String(site.value) : "";
      const items = DATA.products
        .filter(p => {{
          if (siteNeedle && String(p.domain || "") !== siteNeedle) return false;
          if (!needle) return true;
          const specText = Object.entries(p.specs || {{}}).map(([k,v]) => `${{k}}:${{v}}`).join(" ");
          const blob = `${{p.domain}} ${{p.name}} ${{p.price}} ${{p.description || ""}} ${{specText}} ${{p.url}}`.toLowerCase();
          return blob.includes(needle);
        }})
        .slice()
        .sort(cmp);

      updatePie(items);
      tb.innerHTML = "";
      for (const p of items) {{
        const meta = statusMeta(p.available);
        const tr = document.createElement("tr");
        const specs = Object.entries(p.specs || {{}}).map(([k,v]) => `<span class="chip">${{k}}: ${{v}}</span>`).join("");
        const desc = p.description ? `<div class="desc">${{escapeHtml(p.description)}}</div>` : "";
        const staleTag = p.stale ? `<span class="tag-stale">STALE</span>` : "";

        tr.innerHTML = `
          <td data-k="Status">
            <span class="badge"><span class="dot ${{meta.cls}}"></span> ${{meta.label}} ${{staleTag}}</span>
          </td>
          <td data-k="Domain"><span class="muted">${{p.domain}}</span></td>
          <td data-k="Product">
            <div><a class="plink" href="${{p.url}}" target="_blank" rel="noreferrer noopener"><b>${{escapeHtml(p.name)}}</b></a></div>
            ${{desc}}
            <div class="specs">${{specs}}</div>
          </td>
          <td data-k="Price">${{escapeHtml(p.price || "")}}</td>
          <td data-k="Last Seen"><span class="muted">${{escapeHtml(p.last_seen || "")}}</span></td>
          <td data-k="Action"><a class="btn" href="${{p.url}}" target="_blank" rel="noreferrer noopener">Buy Now</a></td>
        `;
        tb.appendChild(tr);
      }}
    }}

    function escapeHtml(s) {{
      return String(s ?? "").replace(/[&<>"]/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}}[c]));
    }}

    q.addEventListener("input", () => render());
    if (site) site.addEventListener("change", () => render());

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
