"""
html_report.py
==============
Standalone HTML report per run: summary, per-cycle table, data dictionary.

Self-contained by design -- no external CSS or scripts -- so a report can be
mailed to a colleague or archived next to the raw export and still render years
later. Plots are deliberately absent; charting arrives with the dashboard.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Optional, Sequence

# Reused deliberately: the workbook and the report must show the same
# columns and the same summary, projected the same way.
from .excel_export import row_values, summary_pairs
from .schema import STIFFNESS_QUALITY, user_facing_cycle_columns

_CSS = """
:root{--bg:#ffffff;--fg:#1a1d21;--muted:#5b6673;--line:#e3e7ec;--head:#1f3864;
--warn-bg:#fff4d6;--warn-fg:#7a5c00;--bad-bg:#fde4e4;--bad-fg:#9c0006;--accent:#1f3864;}
@media (prefers-color-scheme:dark){:root{--bg:#14171a;--fg:#e8ecf1;--muted:#9aa5b1;
--line:#2b3138;--head:#2c4a80;--warn-bg:#3d3312;--warn-fg:#f0d68a;--bad-bg:#3d1f1f;
--bad-fg:#f2a3a3;--accent:#8fb0e8;}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.5rem 4rem;background:var(--bg);color:var(--fg);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;}
.wrap{max-width:1200px;margin:0 auto}
h1{font-size:1.6rem;margin:0 0 .25rem}
h2{font-size:1.15rem;margin:2.5rem 0 .75rem;color:var(--accent)}
.sub{color:var(--muted);margin:0 0 2rem}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:.45rem .6rem;text-align:right;white-space:nowrap;
border-bottom:1px solid var(--line)}
th{background:var(--head);color:#fff;position:sticky;top:0;text-align:right;
font-weight:600;vertical-align:bottom}
th:first-child,td:first-child{text-align:left}
tbody tr:nth-child(even){background:color-mix(in srgb,var(--fg) 3%,transparent)}
.kv{max-width:760px}
.kv td:first-child{color:var(--muted);width:45%}
.kv td{text-align:left;white-space:normal}
.dict td{text-align:left;white-space:normal}
.dict td:nth-child(2){white-space:nowrap;color:var(--muted);text-align:center;width:5rem}
.flag-warn{background:var(--warn-bg);color:var(--warn-fg);font-weight:600}
.flag-bad{background:var(--bad-bg);color:var(--bad-fg);font-weight:600}
.note{background:color-mix(in srgb,var(--accent) 8%,transparent);
border-left:3px solid var(--accent);padding:.7rem .9rem;border-radius:0 6px 6px 0;
margin:.75rem 0;color:var(--muted);font-size:13.5px}
footer{margin-top:3rem;color:var(--muted);font-size:12.5px;
border-top:1px solid var(--line);padding-top:1rem}
"""


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _fmt(value: Any, fmt: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        decimals = len(fmt.split(".")[1]) if "." in fmt else 0
        return f"{value:,.{decimals}f}"
    return _esc(value)


def render(payloads: Sequence[dict], *, title: Optional[str] = None) -> str:
    if not payloads:
        raise ValueError("render needs at least one specimen payload")

    multi = len(payloads) > 1
    has_strain = any(p.get("analysis", {}).get("has_strain") for p in payloads)
    cols = user_facing_cycle_columns(has_strain)
    heading = title or payloads[0].get("specimen", {}).get("label", "Compression test")

    parts: list[str] = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{_esc(heading)}</title><style>{_CSS}</style></head><body><div class='wrap'>",
        f"<h1>{_esc(heading)}</h1>",
        f"<p class='sub'>{len(payloads)} specimen{'s' if multi else ''}"
        f" &middot; analysed {_esc(payloads[0].get('created_utc', ''))}</p>",
    ]

    # --- summary -------------------------------------------------------------
    parts.append("<h2>Summary</h2><div class='scroll'><table class='kv'><tbody>")
    columns = [summary_pairs(p) for p in payloads]
    labels = [p.get("specimen", {}).get("label", "") for p in payloads]
    if multi:
        parts.append("<tr><th style='text-align:left'>Field</th>"
                     + "".join(f"<th>{_esc(l)}</th>" for l in labels) + "</tr>")
    spine = max(columns, key=len)
    for idx, (key, _) in enumerate(spine):
        cells = []
        for pairs in columns:
            value = pairs[idx][1] if idx < len(pairs) else None
            if isinstance(value, float):
                value = f"{value:,.4g}"
            cells.append(f"<td>{_esc(value)}</td>")
        parts.append(f"<tr><td>{_esc(key)}</td>{''.join(cells)}</tr>")
    parts.append("</tbody></table></div>")

    notes = [n for p in payloads for n in p.get("specimen", {}).get("notes", [])]
    for note in dict.fromkeys(notes):
        parts.append(f"<div class='note'>{_esc(note)}</div>")
    if any(p.get("analysis", {}).get("multi_stage") for p in payloads):
        parts.append(
            "<div class='note'>Multi-stage test: peak stress differs between "
            "cycles, so the cycles are stages rather than repeats. Compare them "
            "on the common-band stiffness and the relative hysteresis loss; the "
            "relative-band stiffness and absolute energies are not comparable "
            "across stages.</div>"
        )
    if not has_strain:
        parts.append(
            "<div class='note'>No specimen height h0 was available, so "
            "strain-normalised columns are omitted rather than estimated.</div>"
        )

    # --- cycles --------------------------------------------------------------
    parts.append("<h2>Per-cycle results</h2><div class='scroll'><table><thead><tr>")
    if multi:
        parts.append("<th>Specimen</th>")
    for col in cols:
        unit = f"<br><span style='font-weight:400;opacity:.8'>{_esc(col.unit)}</span>" \
            if col.unit else ""
        parts.append(f"<th>{_esc(col.label)}{unit}</th>")
    parts.append("</tr></thead><tbody>")

    for payload in payloads:
        label = payload.get("specimen", {}).get("label", "")
        for row in payload.get("cycles", []):
            parts.append("<tr>")
            if multi:
                parts.append(f"<td>{_esc(label)}</td>")
            for col, value in zip(cols, row_values(row, cols)):
                cls = ""
                if col.key == STIFFNESS_QUALITY.key and value != "ok":
                    cls = " class='flag-bad'" if value == "none" else " class='flag-warn'"
                parts.append(f"<td{cls}>{_fmt(value, col.fmt)}</td>")
            parts.append("</tr>")
    parts.append("</tbody></table></div>")

    # --- dictionary ----------------------------------------------------------
    parts.append("<h2>What each column means</h2><div class='scroll'>"
                 "<table class='dict'><thead><tr><th style='text-align:left'>Column</th>"
                 "<th>Unit</th><th style='text-align:left'>Definition</th></tr></thead><tbody>")
    for col in cols:
        parts.append(
            f"<tr><td><strong>{_esc(col.label)}</strong></td>"
            f"<td>{_esc(col.unit or '-')}</td><td>{_esc(col.description)}</td></tr>"
        )
    parts.append("</tbody></table></div>")

    parts.append(
        "<footer>Generated by the compression analysis tool. "
        "The JSON record beside this file is the source of truth; this report "
        "and the workbook are rendered from it.</footer></div></body></html>"
    )
    return "".join(parts)


def write_html(payloads: Sequence[dict], path: str | Path,
               *, title: Optional[str] = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(payloads, title=title), encoding="utf-8")
    return path
