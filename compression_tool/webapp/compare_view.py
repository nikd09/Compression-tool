"""Compare: build named groups of specimens -- any specimens, from any
materials, in any combination -- and overlay one metric across the groups'
means. A group is not required to be "a whole material": Group A can be
Material-X's S2+S3 while Group B is Material-Y's S4+S5, so a bad trial run in
one material does not have to drag its whole mean into the comparison."""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from .. import knowledge_base
from ..persistence import Workspace
from ..schema import user_facing_cycle_columns
from .common import CATEGORICAL_LIGHT, connect_readonly, dot, short_tag

MAX_GROUPS = 6  # the categorical palette's practical ceiling for a compare view

# Streamlit's chart toolbar has no Python-side option to drop one of its own
# buttons, so this hides "Copy Vega-Lite spec" with CSS instead: it copies
# the spec as text, not an image, which reads as a broken copy button when
# what someone actually wants is a picture of the chart. Scoped broadly
# (not to a specific container key) because Compare is the only view in
# this app that renders a Vega-Lite chart at all.
_HIDE_SPEC_COPY_CSS = """
<style>
div[data-testid="stElementToolbarButton"]:has(button[aria-label="Copy Vega-Lite spec"]) {
  display: none;
}
</style>
"""


def _default_group_count(n_materials: int) -> int:
    return max(2, min(n_materials, MAX_GROUPS))


def render(ws: Workspace) -> None:
    st.header("Compare")
    conn = connect_readonly(ws)
    if conn is None:
        st.info("Nothing ingested into this workspace yet - use Ingest first.")
        return

    specimens = knowledge_base.list_specimens(conn)
    if specimens.empty:
        st.info("No specimens indexed yet.")
        return

    materials = sorted(specimens["material"].dropna().unique().tolist())
    label_by_id = dict(zip(specimens["specimen_id"], specimens["label"]))
    material_by_id = dict(zip(specimens["specimen_id"], specimens["material"]))
    all_ids = list(specimens["specimen_id"])
    tag_by_id = {
        sid: short_tag(label_by_id[sid], i + 1) for i, sid in enumerate(all_ids)
    }

    def option_label(sid: str) -> str:
        # Tag first, same reasoning as Results: it is what survives a
        # truncated dropdown entry when specimens share a long filename.
        return f"{tag_by_id[sid]} · {material_by_id.get(sid, '-')} - {label_by_id.get(sid, sid)}"

    n_groups = st.number_input(
        "Groups to compare", min_value=1, max_value=MAX_GROUPS,
        value=_default_group_count(len(materials)), step=1,
        help="A group is any set of specimens you pick - it does not have to "
        "be a whole material. Build 'Material A, good runs only' as one group "
        "and 'Material B, S4+S5' as another.",
    )

    st.caption(
        "Each group starts pre-filled with one material's specimens as a "
        "shortcut. Add or remove specimens freely, including from a "
        "different material, to build exactly the comparison you want."
    )

    groups: list[dict] = []
    cols = st.columns(min(int(n_groups), 3))
    for i in range(int(n_groups)):
        default_material = materials[i % len(materials)] if materials else None
        default_ids = (
            specimens.loc[specimens["material"] == default_material, "specimen_id"].tolist()
            if default_material else []
        )
        with cols[i % len(cols)]:
            with st.container(border=True, key=f"card_group_{i}"):
                st.markdown(
                    f'{dot(i)}<span style="font-size:.72rem;font-weight:700;'
                    f'letter-spacing:.06em;text-transform:uppercase;opacity:.65">'
                    f"Group {i + 1}</span>",
                    unsafe_allow_html=True,
                )
                name = st.text_input(
                    "Name", value=default_material or f"Group {i + 1}",
                    key=f"cmp_name_{i}", label_visibility="collapsed",
                    help="Labels this group in the chart legend and the "
                    "membership list below. It has no effect on which "
                    "specimens are in the group, only how this group reads "
                    "once specimens are picked below.",
                )
                chosen = st.multiselect(
                    "Specimens", options=all_ids, default=default_ids,
                    format_func=option_label, key=f"cmp_specimens_{i}",
                    label_visibility="collapsed",
                    placeholder="Pick specimens for this group",
                )
                st.caption(f"{len(chosen)} specimen(s)")
                if chosen:
                    groups.append({"name": name.strip() or f"Group {i + 1}", "ids": chosen})

    if not groups:
        st.info("Pick at least one specimen in at least one group.")
        return

    # Two groups sharing a name are indistinguishable downstream: the chart
    # groups rows BY that name, so identically-named groups merge into one
    # series instead of two, silently. Disambiguate rather than let that
    # happen quietly -- a renamed group is visible; a merged one is not.
    seen: dict[str, int] = {}
    renamed = []
    for g in groups:
        seen[g["name"]] = seen.get(g["name"], 0) + 1
        if seen[g["name"]] > 1:
            new_name = f"{g['name']} ({seen[g['name']]})"
            renamed.append((g["name"], new_name))
            g["name"] = new_name
    if renamed:
        st.warning(
            "Two or more groups had the same name, which the chart cannot "
            "tell apart, so they were renamed to keep them distinct: "
            + "; ".join(f"'{old}' -> '{new}'" for old, new in renamed)
        )

    # Every specimen actually used, fetched once -- groups can overlap or
    # reuse specimens without re-querying per group.
    used_ids = sorted({sid for g in groups for sid in g["ids"]})
    df = knowledge_base.cycles_for_specimens(conn, used_ids)
    if df.empty:
        st.info("No cycles for the selected specimens.")
        return

    has_strain = "MaxStrain_pct" in df.columns and df["MaxStrain_pct"].notna().any()
    options = [
        c for c in user_facing_cycle_columns(has_strain)
        if c.key in df.columns and c.key not in ("Cycle",)
    ]
    metric = st.selectbox(
        "Metric", options, format_func=lambda c: f"{c.label} ({c.unit})" if c.unit else c.label,
    )

    by_specimen = df.set_index("specimen_id") if "specimen_id" in df.columns else None
    rows = []
    for g in groups:
        sub = df[df["specimen_id"].isin(g["ids"])] if by_specimen is not None else df
        for cycle, part in sub.groupby("Cycle"):
            vals = part[metric.key].dropna()
            if vals.empty:
                continue
            rows.append({
                "group": g["name"], "Cycle": cycle,
                "mean": vals.mean(), "sd": vals.std(ddof=0) if len(vals) > 1 else 0.0,
                "n": len(vals),
            })
    agg = pd.DataFrame(rows)
    if agg.empty:
        st.info(f"No values for {metric.label} in the selected specimens.")
        return
    agg["err_lo"] = agg["mean"] - agg["sd"]
    agg["err_hi"] = agg["mean"] + agg["sd"]

    show_spread = st.checkbox(
        "Show spread across specimens (±1 SD)", value=True,
        help="Each bar is a mean. Without the spread beside it, a group of "
        "disagreeing specimens looks exactly like one that agrees.",
    )

    group_order = [g["name"] for g in groups]
    y_title = metric.label + (f" ({metric.unit})" if metric.unit else "")
    tooltip = [
        alt.Tooltip("group:N", title="Group"),
        alt.Tooltip("Cycle:O", title="Cycle"),
        alt.Tooltip("mean:Q", title=metric.label, format=".4g"),
        alt.Tooltip("sd:Q", title="SD", format=".3g"),
        alt.Tooltip("n:Q", title="Specimens"),
    ]
    # Same fixed categorical order the Results dashboard uses -- a group's
    # colour follows its position in the form above, not a value sort, so
    # editing one group's specimens never repaints another group's bars.
    color = alt.Color(
        "group:N", title="Group",
        scale=alt.Scale(domain=group_order, range=CATEGORICAL_LIGHT[: len(group_order)]),
    )

    base = alt.Chart(agg)
    bars = base.mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, stroke=None).encode(
        x=alt.X("Cycle:O", title="Cycle", axis=alt.Axis(labelAngle=0)),
        xOffset=alt.XOffset("group:N", sort=group_order),
        y=alt.Y("mean:Q", title=y_title, axis=alt.Axis(grid=True)),
        color=color,
        tooltip=tooltip,
    )
    layers = [bars]
    if show_spread:
        layers.append(
            base.mark_rule(strokeWidth=1.4, opacity=0.75).encode(
                x=alt.X("Cycle:O"), xOffset=alt.XOffset("group:N", sort=group_order),
                y=alt.Y("err_lo:Q"), y2=alt.Y2("err_hi:Q"), tooltip=tooltip,
            )
        )
    # Direct labels stay SELECTIVE, same threshold and reasoning as the
    # Results dashboard's own bar charts: past three groups, a number on
    # every bar is dozens of labels fighting for the same strip of space,
    # and the fullscreen/PNG-export view -- which is exactly where hovering
    # for the tooltip is not an option -- would be the most crowded of all.
    # Below that threshold, this is also what makes the value actually
    # visible there, which the hover-only tooltip alone cannot do.
    if len(group_order) <= 3:
        layers.append(
            base.mark_text(dy=-7, fontSize=11, fontWeight=600).encode(
                x=alt.X("Cycle:O"), xOffset=alt.XOffset("group:N", sort=group_order),
                y=alt.Y("mean:Q"), text=alt.Text("mean:Q", format=".3g"),
            )
        )
    # A title, matching every chart on the Results tab having one -- this
    # was the one chart in the app without it. Subtitle carries the unit
    # separately, the same title/unit split the Results panels use, rather
    # than folding it into one long title string.
    #
    # width is FIXED, not use_container_width=True: Vega-Lite text is set in
    # absolute pixels and does not scale with the chart's size the way
    # Results' hand-built SVG charts do (every dimension in that template,
    # bar widths down to font sizes, is a function of one base width, so
    # scaling the SVG up for the expanded dialog or a PNG export scales
    # everything together and the proportions never change). Letting this
    # chart stretch to the full page width -- confirmed live, a Vega-Lite
    # spec copied out of this app at "width": 1340 -- grows the plot area
    # while the text stays the same absolute size, so it reads smaller as a
    # fraction of the chart the wider the window is, and the SAME width is
    # what "Download as PNG" captures: a screen-filling chart downloads as a
    # huge image with comparatively tiny text once pasted into a document at
    # a normal page width. A fixed, moderate width keeps that ratio close to
    # what Results' own PNG exports already look like, at every size this
    # spec is ever rendered or exported at, not just on screen.
    chart = (
        alt.layer(*layers)
        .properties(
            width=820, height=480,
            title=alt.TitleParams(
                text=metric.label, subtitle=metric.unit or None,
                # Vega-Lite's fontWeight is a fixed enum (100-900 in steps of
                # 100, or a named keyword) -- not an arbitrary CSS weight
                # like the rest of this app's "650" convention uses.
                fontSize=16, fontWeight=600, anchor="start",
                subtitleFontSize=12, offset=16,
            ),
        )
        .configure_view(stroke=None)
        .configure_axis(labelFontSize=12, titleFontSize=13)
        .configure_legend(labelFontSize=12, titleFontSize=13, symbolSize=110)
    )
    st.markdown(_HIDE_SPEC_COPY_CSS, unsafe_allow_html=True)
    st.altair_chart(chart, use_container_width=False)
    st.caption(
        "Each bar is the mean across that group's chosen specimens, for that "
        "cycle; the whisker is ±1 standard deviation across them. A group of "
        "one specimen shows that specimen's own value and no whisker. Bars "
        "carry a value label at 3 groups or fewer; past that, a number on "
        "every bar is too many labels for the space, so hover a bar for its "
        "exact value instead."
    )

    with st.expander("Group membership and underlying rows"):
        for i, g in enumerate(groups):
            st.markdown(
                f"{dot(i)}**{g['name']}**: " + ", ".join(option_label(s) for s in g["ids"]),
                unsafe_allow_html=True,
            )
        st.dataframe(
            df[["material", "label", "specimen_id", "Cycle", metric.key]],
            use_container_width=True, hide_index=True,
        )
