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
from ..persistence import Workspace, read_json
from .common import connect_readonly, inject_dashboard_lang, short_tag

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "results_dashboard.html"


def render(ws: Workspace) -> None:
    st.header("Results")
    conn = connect_readonly(ws)
    if conn is None:
        st.info("Nothing ingested into this workspace yet - use Ingest first.")
        return

    materials = knowledge_base.materials(conn)
    if not materials:
        st.info("No specimens indexed yet.")
        return
    # Bound to the same "active_material" key Materials writes when a card
    # is clicked, so opening a material there and then switching to Results
    # lands on it directly instead of re-asking. Guarded rather than passed
    # straight to `key=`: a value carried over from a workspace that no
    # longer has this material (switched workspace, or the material was
    # renamed/deleted) would otherwise be rejected outright by selectbox as
    # not among `options`.
    if st.session_state.get("active_material") not in materials:
        st.session_state["active_material"] = materials[0]
    material = st.selectbox("Material", materials, key="active_material")

    specimens = knowledge_base.list_specimens(conn, material)
    if specimens.empty:
        st.info("No specimens for this material.")
        return

    label_by_id = dict(zip(specimens["specimen_id"], specimens["label"]))
    # The short tag goes FIRST: specimens from the same batch share a long
    # common filename prefix, so a truncated dropdown entry or chip cuts off
    # exactly the _S1/_S2 suffix that told them apart, leaving entries that
    # read as identical. Put first, the tag survives that truncation.
    tag_by_id = {
        sid: short_tag(label, i + 1) for i, (sid, label) in enumerate(label_by_id.items())
    }
    default = list(label_by_id)[:COMFORTABLE_SPECIMENS]
    chosen = st.multiselect(
        f"Specimens (1-{MAX_SPECIMENS})",
        options=list(label_by_id),
        default=default,
        format_func=lambda sid: f"{tag_by_id[sid]} - {label_by_id[sid]}",
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
            f"{len(chosen)} specimens selected: the panels widen and the grid "
            "drops to fewer columns to keep the bars legible. Toggle specimens "
            "off inside the dashboard to compact it again."
        )

    row_by_id = specimens.set_index("specimen_id")
    payloads, curves, missing_curve, missing_record = [], [], [], []
    for sid in chosen:
        json_path = ws.root / row_by_id.loc[sid, "json_path"]
        try:
            payloads.append(read_json(json_path))
        except (FileNotFoundError, OSError):
            # The index still lists this specimen but its record is gone
            # from disk -- someone (or something) deleted it outside the
            # app, most likely straight from Explorer/the share, which
            # only ever touches files, never the index that still points
            # at them. Skip it rather than crash the whole tab; Config's
            # "Reindex from disk" is what clears the index of it for good.
            missing_record.append(label_by_id[sid])
            continue
        curve_path = curve_cache_path_for(json_path)
        if curve_path.exists():
            curves.append(read_curve_cache(curve_path))
        else:
            curves.append(None)
            missing_curve.append(label_by_id[sid])

    if missing_record:
        st.error(
            "The index still lists " + ", ".join(missing_record) + " but its "
            "record no longer exists on disk - it was likely deleted outside "
            "the app. Go to Config and use \"Reindex from disk\" to bring the "
            "index back in line with what is actually there."
        )
    if missing_curve:
        st.warning(
            "No curve cache found for: " + ", ".join(missing_curve) + ". "
            "The stress-displacement panel will be empty for it; re-ingest "
            "to generate one. Every other chart is unaffected."
        )
    if not payloads:
        return

    data = build_dashboard_data(payloads, curves)
    html = inject_dashboard_lang(_TEMPLATE_PATH.read_text(encoding="utf-8").replace(
        "/*__DATA__*/", json.dumps(data)
    ))
    # A FIXED, screen-sized frame, scrolling INSIDE itself -- not a frame sized
    # to the content's total height. That earlier approach put the dashboard's
    # own `vh` units (used to size the expanded-chart dialog) against the
    # content height (2000px+), not the screen, so the dialog was never sized
    # to what was actually visible; sizing the frame in Python at a guessed
    # content width also could not track the sidebar being opened or closed,
    # since that changes the real width live in the browser, not in Python.
    # A normal scrollable box has none of that: `vh` means the real viewport,
    # dialogs center in what is actually on screen, and the grid/dialog both
    # already re-measure themselves on resize (see the template's own
    # `resize` listener), so collapsing the sidebar just works.
    components.html(html, height=_FRAME_HEIGHT_PX, scrolling=True)


# A comfortable, fixed viewport for the embedded dashboard. Not a content-fit
# estimate -- seeing a few extra charts per scroll is a minor inconvenience;
# a modal sized against the wrong coordinate space is a broken one.
_FRAME_HEIGHT_PX = 820
