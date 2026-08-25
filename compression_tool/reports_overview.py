"""
reports_overview.py
====================
A single static page, reports/_Overview.html, listing every material with
its headline numbers and a link to its own full report -- so someone who
only wants to see what materials exist and compare them at a glance never
needs the live app running, or even to know which exact name to type where.

A derived rollup, the same kind as reports/<material>.{xlsx,html}
(material_export.py): rebuilt from the index on every ingest, safe to
delete, never the source of truth.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from . import knowledge_base
from .persistence import Workspace, slugify

_TEMPLATE_PATH = Path(__file__).parent / "webapp" / "templates" / "overview.html"


def build_overview(ws: Workspace) -> Optional[Path]:
    """(Re)write reports/_Overview.html from the index.

    Returns the path written, or None if the workspace has no indexed
    specimens yet -- nothing to show, so nothing is written.
    """
    if not ws.db_path.exists():
        return None
    conn = knowledge_base.connect(ws.db_path)
    try:
        specimens = knowledge_base.list_specimens(conn)
    finally:
        conn.close()
    if specimens.empty:
        return None

    rows = []
    for material, group in specimens.groupby("material"):
        peaks = group["global_peak_mpa"].dropna()
        rows.append({
            "material": material,
            # Same slug material_export.py's own reports/<material>.html
            # uses -- this page's links have to land on that exact file.
            "slug": slugify(material),
            "specimens": int(len(group)),
            "runs": int(group["run_dir"].nunique()),
            "meanPeak": float(peaks.mean()) if not peaks.empty else None,
            "lastIngested": str(group["created_utc"].max()),
        })
    rows.sort(key=lambda r: r["material"].casefold())

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
