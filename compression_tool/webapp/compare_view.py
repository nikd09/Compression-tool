"""Compare: build named groups of specimens -- any specimens, from any
materials, in any combination -- and overlay one metric across the groups'
means. A group is not required to be "a whole material": Group A can be
Material-X's S2+S3 while Group B is Material-Y's S4+S5, so a bad trial run in
one material does not have to drag its whole mean into the comparison."""

from __future__ import annotations

import html

import altair as alt
import pandas as pd
import streamlit as st

from .. import knowledge_base
from ..persistence import Workspace
from ..schema import user_facing_cycle_columns
from .common import CATEGORICAL_LIGHT, connect_readonly, dashboard_lang, dot, short_tag

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

# EN/DE strings for this page's own widgets/chart. The language ITSELF now
# comes from the one shared sidebar toggle (common.dashboard_lang(), set by
# common.language_picker() in app.py) rather than a separate widget here --
# see that toggle's own docstring for why. Metric names/units
# (metric.label/metric.unit, from schema.py) are left in English on purpose:
# they are shared with every other view's values table and are out of this
# page's own scope.
_T = {
    "header": {"en": "Compare", "de": "Vergleich"},
    "no_data": {"en": "Nothing ingested into this workspace yet - use Ingest first.",
        "de": "In diesem Workspace wurde noch nichts eingelesen - zuerst unter „Ingest“ eine Datei hinzufügen."},
    "no_specimens": {"en": "No specimens indexed yet.", "de": "Noch keine Proben indiziert."},
    "groups_label": {"en": "Groups to compare", "de": "Zu vergleichende Gruppen"},
    "groups_help": {
        "en": "A group is any set of specimens you pick - it does not have to "
              "be a whole material. Build 'Material A, good runs only' as one group "
              "and 'Material B, S4+S5' as another.",
        "de": "Eine Gruppe ist eine beliebige Auswahl an Proben - sie muss kein "
              "ganzes Material sein. Zum Beispiel „Material A, nur gute Läufe“ als "
              "eine Gruppe und „Material B, S4+S5“ als eine andere."},
    "caption_groups": {
        "en": "Each group starts pre-filled with one material's specimens as a "
              "shortcut. The material filter below narrows the specimen list to "
              "one material at a time - useful once a workspace has many; pick "
              "'All materials' to combine specimens across materials in one group.",
        "de": "Jede Gruppe wird zunächst mit den Proben eines Materials "
              "vorbefüllt. Der Materialfilter unten schränkt die Probenliste "
              "jeweils auf ein Material ein - nützlich bei vielen Materialien im "
              "Workspace; „Alle Materialien“ wählen, um Proben materialübergreifend "
              "in einer Gruppe zu kombinieren."},
    "all_materials": {"en": "All materials", "de": "Alle Materialien"},
    "group_n": {"en": "Group {n}", "de": "Gruppe {n}"},
    "name_help": {
        "en": "Labels this group in the chart legend and the membership list "
              "below. It has no effect on which specimens are in the group, only "
              "how this group reads once specimens are picked below.",
        "de": "Beschriftet diese Gruppe in der Diagrammlegende und der "
              "Mitgliederliste unten. Hat keinen Einfluss darauf, welche Proben "
              "in der Gruppe sind, nur darauf, wie die Gruppe angezeigt wird, "
              "sobald unten Proben ausgewählt sind."},
    "material_filter_help": {
        "en": "Narrows the specimen list below to one material. Switching this "
              "clears this group's current pick, so a specimen from a material "
              "you filter away is never silently kept in the group.",
        "de": "Schränkt die Probenliste unten auf ein Material ein. Ein Wechsel "
              "hier löscht die aktuelle Auswahl dieser Gruppe, damit eine Probe "
              "aus einem herausgefilterten Material nie stillschweigend in der "
              "Gruppe bleibt."},
    "specimens_placeholder": {"en": "Pick specimens for this group",
        "de": "Proben für diese Gruppe auswählen"},
    "n_specimens": {"en": "{n} specimen(s)", "de": "{n} Probe(n)"},
    "pick_at_least_one": {"en": "Pick at least one specimen in at least one group.",
        "de": "Mindestens eine Probe in mindestens einer Gruppe auswählen."},
    "renamed_warning": {
        "en": "Two or more groups had the same name, which the chart cannot "
              "tell apart, so they were renamed to keep them distinct: {changes}",
        "de": "Zwei oder mehr Gruppen hatten denselben Namen, den das Diagramm "
              "nicht unterscheiden kann, daher wurden sie zur Unterscheidung "
              "umbenannt: {changes}"},
    "no_cycles": {"en": "No cycles for the selected specimens.",
        "de": "Keine Zyklen für die ausgewählten Proben."},
    "metric_label": {"en": "Metric", "de": "Kennzahl"},
    "no_values": {"en": "No values for {label} in the selected specimens.",
        "de": "Keine Werte für {label} in den ausgewählten Proben."},
    "spread_checkbox": {"en": "Show spread across specimens (±1 SD)",
        "de": "Streuung über die Proben anzeigen (±1 SD)"},
    "spread_help": {
        "en": "Each bar is a mean. Without the spread beside it, a group of "
              "disagreeing specimens looks exactly like one that agrees.",
        "de": "Jeder Balken ist ein Mittelwert. Ohne die Streuung daneben sieht "
              "eine Gruppe uneinheitlicher Proben genauso aus wie eine "
              "übereinstimmende."},
    "tooltip_group": {"en": "Group", "de": "Gruppe"},
    "tooltip_cycle": {"en": "Cycle", "de": "Zyklus"},
    "tooltip_sd": {"en": "SD", "de": "SD"},
    "tooltip_specimens": {"en": "Specimens", "de": "Proben"},
    "axis_cycle": {"en": "Cycle", "de": "Zyklus"},
    "chart_caption": {
        "en": "Each bar is the mean across that group's chosen specimens, for that "
              "cycle; the whisker is ±1 standard deviation across them. A group of "
              "one specimen shows that specimen's own value and no whisker. Bars "
              "carry a value label at 3 groups or fewer; past that, a number on "
              "every bar is too many labels for the space, so hover a bar for its "
              "exact value instead.",
        "de": "Jeder Balken ist der Mittelwert über die ausgewählten Proben dieser "
              "Gruppe, für diesen Zyklus; die Fehlerlinie ist ±1 Standardabweichung "
              "über diese Proben. Eine Gruppe mit nur einer Probe zeigt deren "
              "eigenen Wert und keine Fehlerlinie. Balken tragen bei 3 Gruppen oder "
              "weniger eine Wertbeschriftung; darüber sind Zahlen auf jedem Balken "
              "zu viele Beschriftungen für den Platz - stattdessen einen Balken für "
              "seinen genauen Wert überfahren."},
    "expander_title": {"en": "Group membership and underlying rows",
        "de": "Gruppenzugehörigkeit und zugrunde liegende Werte"},
}


def _default_group_count(n_materials: int) -> int:
    return max(2, min(n_materials, MAX_GROUPS))


def render(ws: Workspace) -> None:
    lang = dashboard_lang()

    def L(key: str, **kw) -> str:
        s = _T[key][lang]
        return s.format(**kw) if kw else s

    st.header(L("header"))
    conn = connect_readonly(ws)
    if conn is None:
        st.info(L("no_data"))
        return

    specimens = knowledge_base.list_specimens(conn)
    if specimens.empty:
        st.info(L("no_specimens"))
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
        L("groups_label"), min_value=1, max_value=MAX_GROUPS,
        value=_default_group_count(len(materials)), step=1,
        help=L("groups_help"),
    )

    st.caption(L("caption_groups"))

    _ALL_MATERIALS = L("all_materials")
    material_options = [_ALL_MATERIALS] + materials

    groups: list[dict] = []
    cols = st.columns(min(int(n_groups), 3))
    for i in range(int(n_groups)):
        default_material = materials[i % len(materials)] if materials else None
        with cols[i % len(cols)]:
            with st.container(border=True, key=f"card_group_{i}"):
                st.markdown(
                    f'{dot(i)}<span style="font-size:.72rem;font-weight:700;'
                    f'letter-spacing:.06em;text-transform:uppercase;opacity:.65">'
                    f"{L('group_n', n=i + 1)}</span>",
                    unsafe_allow_html=True,
                )
                name = st.text_input(
                    "Name", value=default_material or L("group_n", n=i + 1),
                    key=f"cmp_name_{i}", label_visibility="collapsed",
                    help=L("name_help"),
                )
                filter_choice = st.selectbox(
                    "Material filter", material_options,
                    index=material_options.index(default_material) if default_material else 0,
                    key=f"cmp_material_filter_{i}", label_visibility="collapsed",
                    help=L("material_filter_help"),
                )
                if filter_choice == _ALL_MATERIALS:
                    filtered_ids, default_ids = all_ids, []
                else:
                    filtered_ids = specimens.loc[
                        specimens["material"] == filter_choice, "specimen_id"
                    ].tolist()
                    default_ids = filtered_ids
                # Keyed on the filter choice too, not just the group index:
                # a widget's session_state value is not automatically pruned
                # to a narrower `options` on rerun, and Streamlit raises
                # rather than silently drop a now-out-of-range selection.
                # Folding the filter into the key gives the multiselect a
                # fresh widget identity (and its own clean default) the
                # instant the filter changes, instead of carrying a stale
                # selection into options it no longer belongs to.
                chosen = st.multiselect(
                    "Specimens", options=filtered_ids, default=default_ids,
                    format_func=option_label, key=f"cmp_specimens_{i}_{filter_choice}",
                    label_visibility="collapsed",
                    placeholder=L("specimens_placeholder"),
                )
                st.caption(L("n_specimens", n=len(chosen)))
                if chosen:
                    groups.append({"name": name.strip() or L("group_n", n=i + 1), "ids": chosen})

    if not groups:
        st.info(L("pick_at_least_one"))
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
        st.warning(L(
            "renamed_warning",
            changes="; ".join(f"'{old}' -> '{new}'" for old, new in renamed),
        ))

    # Every specimen actually used, fetched once -- groups can overlap or
    # reuse specimens without re-querying per group.
    used_ids = sorted({sid for g in groups for sid in g["ids"]})
    df = knowledge_base.cycles_for_specimens(conn, used_ids)
    if df.empty:
        st.info(L("no_cycles"))
        return

    has_strain = "MaxStrain_pct" in df.columns and df["MaxStrain_pct"].notna().any()
    options = [
        c for c in user_facing_cycle_columns(has_strain)
        if c.key in df.columns and c.key not in ("Cycle",)
    ]
    metric = st.selectbox(
        L("metric_label"), options, format_func=lambda c: f"{c.label} ({c.unit})" if c.unit else c.label,
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
        st.info(L("no_values", label=metric.label))
        return
    agg["err_lo"] = agg["mean"] - agg["sd"]
    agg["err_hi"] = agg["mean"] + agg["sd"]

    show_spread = st.checkbox(
        L("spread_checkbox"), value=True, help=L("spread_help"),
    )

    group_order = [g["name"] for g in groups]
    y_title = metric.label + (f" ({metric.unit})" if metric.unit else "")
    tooltip = [
        alt.Tooltip("group:N", title=L("tooltip_group")),
        alt.Tooltip("Cycle:O", title=L("tooltip_cycle")),
        alt.Tooltip("mean:Q", title=metric.label, format=".4g"),
        alt.Tooltip("sd:Q", title=L("tooltip_sd"), format=".3g"),
        alt.Tooltip("n:Q", title=L("tooltip_specimens")),
    ]
    # Same fixed categorical order the Results dashboard uses -- a group's
    # colour follows its position in the form above, not a value sort, so
    # editing one group's specimens never repaints another group's bars.
    color = alt.Color(
        "group:N", title=L("tooltip_group"),
        scale=alt.Scale(domain=group_order, range=CATEGORICAL_LIGHT[: len(group_order)]),
    )

    base = alt.Chart(agg)
    bars = base.mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3, stroke=None).encode(
        x=alt.X("Cycle:O", title=L("axis_cycle"), axis=alt.Axis(labelAngle=0)),
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
    st.caption(L("chart_caption"))

    with st.expander(L("expander_title")):
        for i, g in enumerate(groups):
            # html.escape on the two pieces that are not fixed templates:
            # the group name (typed freely, above) and each specimen's own
            # material/label (free text at Ingest time, persisted). Neither
            # is sanitised anywhere upstream, and this line is the one place
            # in Compare that puts either through unsafe_allow_html.
            members = ", ".join(html.escape(option_label(s)) for s in g["ids"])
            st.markdown(
                f"{dot(i)}**{html.escape(g['name'])}**: " + members,
                unsafe_allow_html=True,
            )
        st.dataframe(
            df[["material", "label", "specimen_id", "Cycle", metric.key]],
            use_container_width=True, hide_index=True,
        )
