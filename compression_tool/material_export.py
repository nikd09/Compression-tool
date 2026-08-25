"""
material_export.py
===================
One combined Excel workbook and one standalone HTML dashboard (charts baked
in, no server needed to view it) per material -- covering every specimen
ever ingested for it, regardless of which run or ingest session it came
from. A derived rollup, like knowledge_base.db: rebuilt from the indexed
specimens on every call, safe to delete, never the source of truth.

Written to <workspace>/reports/<material-slug>.xlsx and .html, deliberately
apart from the per-run archive under processed_output/<material>_<date>/ --
so "everything for this material" is always one predictable pair of files,
not something to reassemble by hand across however many sessions built it up.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Optional

from . import excel_export, knowledge_base
from .curve_cache import curve_cache_path_for, read_curve_cache
from .dashboard_data import MAX_SPECIMENS, build_dashboard_data
from .persistence import Workspace, read_json, slugify

_TEMPLATE_PATH = (
    Path(__file__).parent / "webapp" / "templates" / "results_dashboard.html"
)


def export_material(ws: Workspace, material: str) -> dict[str, Optional[Path]]:
    """(Re)write the combined workbook and dashboard for `material`.

    Returns {"xlsx": Path, "html": Path}, or {"xlsx": None, "html": None} if
    the material has no indexed specimens -- nothing to write. Overwrites
    whatever was there before; callers never need to clean up a stale copy.
    """
    if not ws.db_path.exists():
        return {"xlsx": None, "html": None}
    conn = knowledge_base.connect(ws.db_path)
    try:
        specimens = knowledge_base.list_specimens(conn, material)
    finally:
        conn.close()
    if specimens.empty:
        return {"xlsx": None, "html": None}

    # Oldest first, so a capped dashboard slice (below) keeps the newest runs
    # rather than whichever sorted first.
    specimens = specimens.sort_values("created_utc")
    json_paths = [ws.root / p for p in specimens["json_path"]]
    payloads = [read_json(p) for p in json_paths]

    out_dir = ws.root / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = slugify(material)

    # No colour-palette limit on a table -- every specimen ever ingested for
    # this material goes in the workbook, no matter how many.
    xlsx_path = excel_export.write_workbook(payloads, out_dir / f"{stem}.xlsx")

    dash_payloads, dash_json_paths = payloads, json_paths
    truncated = len(payloads) > MAX_SPECIMENS
    if truncated:
        # The dashboard has a hard MAX_SPECIMENS-colour ceiling (see
        # dashboard_data.py) -- something has to be left out of the CHART
        # once a material outgrows it. Newest specimens win the slots; every
        # specimen stays in the Excel above regardless.
        dash_payloads = payloads[-MAX_SPECIMENS:]
        dash_json_paths = json_paths[-MAX_SPECIMENS:]

    curves = [
        read_curve_cache(cp) if (cp := curve_cache_path_for(jp)).exists() else None
        for jp in dash_json_paths
    ]
    data = build_dashboard_data(dash_payloads, curves)
    # build_dashboard_data takes sourceFilename from the FIRST specimen,
    # correct for one ingest run (they share a source file) but misleading
    # here: this dashboard combines specimens from however many separate
    # sessions. Overridden so the on-page title and the downloaded-PNG
    # filename both read as the material, not one arbitrary specimen's file.
    data["sourceFilename"] = f"{material} (combined, {len(dash_payloads)} specimens)"
    page = _TEMPLATE_PATH.read_text(encoding="utf-8").replace(
        "/*__DATA__*/", json.dumps(data)
    ).replace(
        "<title>Compression Results</title>",
        f"<title>{html.escape(material)} - Compression Results</title>",
    )
    if truncated:
        page = (
            f"<!-- Showing the {MAX_SPECIMENS} most recent of {len(payloads)} "
            f"specimens ingested for this material -- the dashboard's colour "
            f"palette has a hard {MAX_SPECIMENS}-series limit. Every specimen "
            f"is still in the paired Excel workbook. -->\n" + page
        )
    html_path = out_dir / f"{stem}.html"
    html_path.write_text(page, encoding="utf-8")

    return {"xlsx": xlsx_path, "html": html_path}
