"""Compare: overlay one metric across materials, averaged per cycle across
whatever specimens each material has. The one view HANDOFF.md scoped as
needing cross-material data rather than a single record, so it is the one
view built on `knowledge_base.cycles_for_materials()` instead of a JSON file."""

from __future__ import annotations

import altair as alt
import streamlit as st

from .. import knowledge_base
from ..schema import user_facing_cycle_columns
from .common import connect_readonly, polish, workspace_picker


def render() -> None:
    polish()
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
        .agg(mean="mean", sd="std", n="count", lo="min", hi="max")
    )
    # std() is NaN for a single specimen; the whisker is simply absent there
    # rather than being drawn as zero spread, which would claim agreement that
    # was never measured.
    agg["sd"] = agg["sd"].fillna(0.0)
    agg["err_lo"] = agg["mean"] - agg["sd"]
    agg["err_hi"] = agg["mean"] + agg["sd"]

    show_spread = st.checkbox(
        "Show spread across specimens (±1 SD)", value=True,
        help="Each bar is a mean. Without the spread beside it, two materials "
        "whose specimens disagree wildly look exactly like two that agree.",
    )

    y_title = metric.label + (f" ({metric.unit})" if metric.unit else "")
    tooltip = [
        alt.Tooltip("material:N", title="Material"),
        alt.Tooltip("Cycle:O", title="Cycle"),
        alt.Tooltip("mean:Q", title=metric.label, format=".4g"),
        alt.Tooltip("sd:Q", title="SD", format=".3g"),
        alt.Tooltip("n:Q", title="Specimens"),
    ]

    base = alt.Chart(agg)
    # Grouped bars per cycle, one bar per material -- the same idiom as the
    # Results dashboard, so a reader moving between the two tabs is not
    # relearning the chart. Colours come from the theme's categorical slots.
    bars = base.mark_bar(
        cornerRadiusTopLeft=3, cornerRadiusTopRight=3, stroke=None,
    ).encode(
        x=alt.X("Cycle:O", title="Cycle", axis=alt.Axis(labelAngle=0)),
        xOffset=alt.XOffset("material:N", title="Material"),
        y=alt.Y("mean:Q", title=y_title, axis=alt.Axis(grid=True)),
        color=alt.Color("material:N", title="Material"),
        tooltip=tooltip,
    )
    layers = [bars]
    if show_spread:
        layers.append(
            base.mark_rule(strokeWidth=1.4, opacity=0.75).encode(
                x=alt.X("Cycle:O"),
                xOffset=alt.XOffset("material:N"),
                y=alt.Y("err_lo:Q"), y2=alt.Y2("err_hi:Q"),
                tooltip=tooltip,
            )
        )
    chart = alt.layer(*layers).properties(height=430).configure_view(stroke=None)
    st.altair_chart(chart, use_container_width=True)
    st.caption(
        "Each bar is the mean of every specimen indexed under that material, "
        "for that cycle; the whisker is ±1 standard deviation across them. A "
        "material with one specimen shows that specimen's own value and no "
        "whisker."
    )

    with st.expander("Underlying rows"):
        st.dataframe(
            df[["material", "label", "specimen_id", "Cycle", metric.key]],
            use_container_width=True, hide_index=True,
        )
