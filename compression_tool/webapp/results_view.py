"""Results: pick a material and its specimens, render the validated
grouped-bar dashboard against their real records and curve caches."""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from .. import knowledge_base
from ..curve_cache import curve_cache_path_for, read_curve_cache
from ..dashboard_data import COMFORTABLE_SPECIMENS, MAX_SPECIMENS, build_dashboard_data
from ..persistence import read_json
from .common import connect_readonly, polish, workspace_picker

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "results_dashboard.html"


def render() -> None:
    polish()
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
    default = list(label_by_id)[:COMFORTABLE_SPECIMENS]
    chosen = st.multiselect(
        f"Specimens (1–{MAX_SPECIMENS})",
        options=list(label_by_id),
        default=default,
        format_func=lambda sid: label_by_id[sid],
        max_selections=MAX_SPECIMENS,
        help="Every specimen selected here gets its own colour (S1, S2, …) plus "
        "a mean across them. Individual specimens can then be toggled on and "
        "off inside the dashboard without reloading. The palette has "
        f"{MAX_SPECIMENS} distinct colours and will not reuse one; the charts "
        f"read most comfortably up to about {COMFORTABLE_SPECIMENS}.",
    )
    if not chosen:
        st.info("Pick at least one specimen.")
        return
    if len(chosen) > COMFORTABLE_SPECIMENS:
        st.caption(
            f"{len(chosen)} specimens selected — the panels widen and the grid "
            "drops to fewer columns to keep the bars legible. Toggle specimens "
            "off inside the dashboard to compact it again."
        )

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
    components.html(html, height=_frame_height(len(payloads)), scrolling=True)


# Panel geometry, mirrored from the template so the frame is tall enough to
# hold what the template will lay out. The template is authoritative -- these
# constants exist only to size the iframe, and being a little generous costs
# nothing while being short forces a scrollbar inside a scrollbar.
_MIN_BAR, _BAR_GAP, _GROUP_PAD, _PANEL_CAP = 5, 2, 8, 530
# The app runs layout="wide" with a ~300px sidebar, so this is the content
# width to expect on a laptop. It is only an estimate: the grid reflows against
# the real width, and `scrolling=True` absorbs the difference either way.
_ASSUMED_CONTENT_PX = 1150


def _frame_height(n_specimens: int) -> int:
    """How tall the embedded dashboard needs to be.

    Streamlit fixes an iframe's height up front, and the template's grid drops
    to fewer columns as specimens are added, so a constant height either clips
    a wide run or leaves a screen of blank under a narrow one.

    A panel's drawn height follows the COLUMN width, not the minimum that
    chose the column count -- the SVG scales to whatever the cell ends up
    being. Calibrated against the rendered page at 1150px, where it now runs
    16-75px long across 2, 4, 6 and 8 specimens: never short, never by a
    screenful.
    """
    n_series = n_specimens + (1 if n_specimens > 1 else 0)   # + the Avg series
    cell_min = max(340, min(_PANEL_CAP,
                            9 * (n_series * (_MIN_BAR + _BAR_GAP) + _GROUP_PAD) + 60))
    # The grid lays out inside the page's own padding, and auto-fit fits N
    # columns when N*cell_min + (N-1)*gap <= width -- so the gap has to be in
    # the division, not left out of it.
    inner = _ASSUMED_CONTENT_PX - 2 * _PAGE_PAD_PX
    cols = max(1, (inner + _GRID_GAP_PX) // (cell_min + _GRID_GAP_PX))
    bar_panels = 9                       # every metric panel except the curves
    rows = -(-bar_panels // cols)        # ceil
    col_w = (inner - (cols - 1) * _GRID_GAP_PX) / cols
    panel_h = col_w / _PANEL_ASPECT + _CELL_CHROME_PX
    loop_h = inner / 1000 * _LOOP_VIEWBOX_H + _CELL_CHROME_PX
    return int(_PAGE_CHROME_PX + rows * panel_h + loop_h + _SAFETY_PX)


_GRID_GAP_PX = 14          # .grid gap, 0.85rem
_PAGE_PAD_PX = 18          # .wrap horizontal padding, 1.15rem
_PANEL_ASPECT = 360 / 220  # RATIO in the template
_CELL_CHROME_PX = 24       # cell padding + title block above each plot
_LOOP_VIEWBOX_H = 400      # the curves panel's viewBox height at width 1000
_PAGE_CHROME_PX = 545      # header, control bar, footer, page padding
_SAFETY_PX = 100
