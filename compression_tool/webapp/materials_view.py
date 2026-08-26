"""Materials: a library view of what exists in this workspace -- one card
per material, with the properties that matter when scanning the whole set,
not a deep dive into any one of them. Results and Compare already own the
deep dive, per material and across materials respectively; this tab is
deliberately just the index, so it stays scannable at a glance."""

from __future__ import annotations

import html

import streamlit as st

from ..persistence import Workspace
from ..reports_overview import material_rows


def render(ws: Workspace) -> None:
    st.header("Materials")
    st.caption("Every material in this workspace, at a glance.")

    rows = material_rows(ws)
    if not rows:
        st.info("Nothing ingested into this workspace yet — use Ingest first.")
        return

    for row in rows:
        with st.container(border=True):
            added = row["dateAdded"]
            st.markdown(
                '<div style="display:flex;align-items:baseline;'
                'justify-content:space-between;gap:.6rem">'
                f'<span style="font-size:1.05rem;font-weight:650;'
                f'letter-spacing:-.012em">{html.escape(row["material"])}</span>'
                '<span style="flex:none;font-size:.72rem;opacity:.6;'
                f'white-space:nowrap">Added {added[:10] if len(added) >= 10 else added}</span>'
                "</div>",
                unsafe_allow_html=True,
            )
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Specimens", row["specimens"])
            c2.metric("Runs", row["runs"])
            c3.metric(
                "Peak stress",
                f"{row['meanPeak']:.0f} MPa" if row["meanPeak"] is not None else "—",
            )
            c4.metric(
                "Thickness (h0)",
                f"{row['meanH0']:.3f} mm" if row["meanH0"] is not None else "—",
            )
