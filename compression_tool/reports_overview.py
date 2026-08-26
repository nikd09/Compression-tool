"""
reports_overview.py
====================
Per-material library stats -- specimens, runs, mean peak stress, mean
thickness (h0), and the date each material was first ingested -- and the
static page built from them, reports/_Overview.html: someone who only wants
to see what materials exist never needs the live app running, or even to
know which exact name to type where.

`material_rows()` is also what the webapp's Materials tab (materials_view.py)
renders from, so the two never carry different numbers for the same
workspace -- one computation, two presentations.

The static page is a derived rollup, the same kind as
reports/<material>.{xlsx,html} (material_export.py): rebuilt from the index
on every ingest, safe to delete, never the source of truth.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from . import knowledge_base
from .persistence import Workspace, slugify

_TEMPLATE_PATH = Path(__file__).parent / "webapp" / "templates" / "overview.html"


def material_rows(ws: Workspace) -> list[dict]:
    """One row per material: specimens, runs, mean peak stress, mean h0,
    and the earliest `created_utc` among its specimens -- the date this
    material was first added to the workspace, not its most recent
    activity. Empty list if the workspace has no indexed specimens yet.
    """
    if not ws.db_path.exists():
        return []
    conn = knowledge_base.connect(ws.db_path)
    try:
        specimens = knowledge_base.list_specimens(conn)
    finally:
        conn.close()
    if specimens.empty:
        return []

    rows = []
    for material, group in specimens.groupby("material"):
        peaks = group["global_peak_mpa"].dropna()
        h0s = group["h0_mm"].dropna()
        rows.append({
            "material": material,
            # Same slug material_export.py's own reports/<material>.html
            # uses -- this page's links have to land on that exact file.
            "slug": slugify(material),
            "specimens": int(len(group)),
            "runs": int(group["run_dir"].nunique()),
            "meanPeak": float(peaks.mean()) if not peaks.empty else None,
            "meanH0": float(h0s.mean()) if not h0s.empty else None,
            "dateAdded": str(group["created_utc"].min()),
        })
    rows.sort(key=lambda r: r["material"].casefold())
    return rows


def build_overview(ws: Workspace) -> Optional[Path]:
    """(Re)write reports/_Overview.html from the index.

    Returns the path written, or None if the workspace has no indexed
    specimens yet -- nothing to show, so nothing is written.
    """
    rows = material_rows(ws)
    if not rows:
        return None

    out_dir = ws.root / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    page = _TEMPLATE_PATH.read_text(encoding="utf-8").replace(
        "/*__DATA__*/", json.dumps({"materials": rows})
    )
    path = out_dir / "_Overview.html"
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text(page, encoding="utf-8")
    os.replace(tmp, path)
    return path
