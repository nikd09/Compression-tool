"""Results: pick a material and up to two specimens, render the validated
grouped-bar dashboard against their real records and curve caches."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from .. import knowledge_base
from ..curve_cache import curve_cache_path_for, read_curve_cache
from ..dashboard_data import MAX_SPECIMENS, build_dashboard_data
from ..persistence import read_json
from .common import connect_readonly, workspace_picker

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "results_dashboard.html"


def render() -> None:
    st.header("Results")
    ws = workspace_picker()
    conn = connect_readonly(ws)
    if conn is None:
        st.info("Nothing ingested into this workspace yet — use Ingest first.")
        return

    materials = knowledge_base.materials(conn)
    if not materials:
        st.info("No specimens indexed yet.")
        return
    material = st.selectbox("Material", materials)

    specimens = knowledge_base.list_specimens(conn, material)
    if specimens.empty:
        st.info("No specimens for this material.")
        return

    label_by_id = dict(zip(specimens["specimen_id"], specimens["label"]))
    chosen = st.multiselect(
        f"Specimens (1–{MAX_SPECIMENS})",
        options=list(label_by_id),
        format_func=lambda sid: label_by_id[sid],
        max_selections=MAX_SPECIMENS,
        help="The chart set is built for one specimen, or two shown side by "
        "side with their mean (S1 / S2 / Avg) — the grouped-bar idiom this "
        "dashboard standardised on. More than two would have nowhere "
        "distinct left in the colour palette.",
    )
    if not chosen:
        st.info("Pick at least one specimen.")
        return

    row_by_id = specimens.set_index("specimen_id")
    payloads, curves, missing_curve = [], [], []
    for sid in chosen:
        json_path = ws.root / row_by_id.loc[sid, "json_path"]
        payloads.append(read_json(json_path))
        curve_path = curve_cache_path_for(json_path)
        if curve_path.exists():
            curves.append(read_curve_cache(curve_path))
        else:
            curves.append(None)
            missing_curve.append(label_by_id[sid])

    if missing_curve:
        st.warning(
            "No curve cache found for: " + ", ".join(missing_curve) + ". "
            "The stress-displacement panel will be empty for it — re-ingest "
            "to generate one; every other chart is unaffected."
        )

    data = build_dashboard_data(payloads, curves)
    html = _TEMPLATE_PATH.read_text(encoding="utf-8").replace(
        "/*__DATA__*/", json.dumps(data)
    )
    components.html(html, height=2500, scrolling=True)
