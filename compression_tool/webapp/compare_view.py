"""Compare: overlay one metric across materials, averaged per cycle across
whatever specimens each material has. The one view HANDOFF.md scoped as
needing cross-material data rather than a single record, so it is the one
view built on `knowledge_base.cycles_for_materials()` instead of a JSON file."""

from __future__ import annotations

import altair as alt
import streamlit as st

from .. import knowledge_base
from ..schema import user_facing_cycle_columns
from .common import connect_readonly, workspace_picker


def render() -> None:
    st.header("Compare")
    ws = workspace_picker()
    conn = connect_readonly(ws)
    if conn is None:
        st.info("Nothing ingested into this workspace yet — use Ingest first.")
        return

    materials = knowledge_base.materials(conn)
    if len(materials) < 1:
        st.info("No specimens indexed yet.")
        return

    chosen = st.multiselect("Materials", materials, default=materials[: min(3, len(materials))])
    if not chosen:
        st.info("Pick at least one material.")
        return

    df = knowledge_base.cycles_for_materials(conn, chosen)
    if df.empty:
        st.info("No cycles for the selected materials.")
        return

    # Numeric, user-facing columns only -- the same set the workbook's Cycles
    # sheet shows, so a column picked here means what it means there too.
    has_strain = "MaxStrain_pct" in df.columns and df["MaxStrain_pct"].notna().any()
    options = [
        c for c in user_facing_cycle_columns(has_strain)
        if c.key in df.columns and c.key not in ("Cycle",)
    ]
    metric = st.selectbox(
        "Metric", options, format_func=lambda c: f"{c.label} ({c.unit})" if c.unit else c.label,
    )

    agg = (
        df.groupby(["material", "Cycle"], as_index=False)[metric.key]
        .mean()
        .rename(columns={metric.key: "value"})
    )

    chart = (
        alt.Chart(agg)
        .mark_line(point=True)
        .encode(
            x=alt.X("Cycle:O", title="Cycle"),
            y=alt.Y("value:Q", title=f"{metric.label}" + (f" ({metric.unit})" if metric.unit else "")),
            color=alt.Color("material:N", title="Material"),
            tooltip=["material", "Cycle", alt.Tooltip("value:Q", format=".3f")],
        )
        .properties(height=420)
    )
    st.altair_chart(chart, use_container_width=True)
    st.caption(
        "Each point is the mean of every specimen indexed under that material, "
        "for that cycle. A material with only one specimen shows that "
        "specimen's own value."
    )

    with st.expander("Underlying rows"):
        st.dataframe(
            df[["material", "label", "specimen_id", "Cycle", metric.key]],
            use_container_width=True, hide_index=True,
        )
