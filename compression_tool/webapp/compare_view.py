"""Compare: build named groups of specimens -- any specimens, from any
materials, in any combination -- and overlay one metric across the groups'
means, plotted against the stress of each step, plus the groups' raw
stress-displacement curves. A group is not required to be "a whole material": Group A can be
Material-X's S2+S3 while Group B is Material-Y's S4+S5, so a bad trial run in
one material does not have to drag its whole mean into the comparison."""

from __future__ import annotations

import html

import altair as alt
import pandas as pd
import streamlit as st

from .. import knowledge_base
from ..curve_cache import curve_cache_path_for, read_curve_cache
from ..persistence import Workspace
from ..schema import column_label_de, user_facing_cycle_columns
from .common import CATEGORICAL_LIGHT, connect_readonly, dashboard_lang, dot, short_tag

MAX_GROUPS = 6  # the categorical palette's practical ceiling for a compare view

# Each cycle is placed on the stress axis at its peak stress, rounded to this
# step, so specimens of one group that reached 449.8 and 450.3 MPa share one
# point, and a 6-cycle and a 9-cycle run line up at 50, 100 ... 300 MPa.
STRESS_LEVEL_STEP_MPA = 5.0

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
# see that toggle's own docstring for why. Metric names come from schema.py
# (column_label_de for German); units are not translated.
_T = {
    "header": {"en": "Compare", "de": "Vergleich"},
    "no_data": {"en": "Nothing ingested into this workspace yet - use Ingest first.",
        "de": "In diesem Workspace wurde noch nichts eingelesen - zuerst unter „Einlesen“ eine Datei hinzufügen."},
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
    "tooltip_stress": {"en": "Stress step (MPa)", "de": "Laststufe (MPa)"},
    "tooltip_sd": {"en": "SD", "de": "SD"},
    "tooltip_specimens": {"en": "Specimens", "de": "Proben"},
    "axis_stress": {"en": "Peak stress of the step (MPa)", "de": "Spitzenspannung der Stufe (MPa)"},
    "chart_caption": {
        "en": "Each point is the group's mean at that stress step; the vertical bar "
              "is ±1 standard deviation across its specimens.",
        "de": "Jeder Punkt ist der Mittelwert der Gruppe auf dieser Laststufe; der "
              "senkrechte Strich ist ±1 Standardabweichung über ihre Proben."},
    "curves_heading": {"en": "Stress-displacement curves", "de": "Spannungs-Weg-Kurven"},
    "curves_step": {"en": "Stress-displacement curves: steps shown",
        "de": "Spannungs-Weg-Kurven: angezeigte Stufen"},
    "curves_all_steps": {"en": "All steps", "de": "Alle Stufen"},
    "curves_step_option": {"en": "{mpa:g} MPa step", "de": "Stufe {mpa:g} MPa"},
    "curves_caption": {
        "en": "Raw loading and unloading curves of every specimen in the groups, "
              "coloured by group.",
        "de": "Rohe Be- und Entlastungskurven aller Proben der Gruppen, nach Gruppe "
              "eingefärbt."},
    "curves_missing": {"en": "No curve data for: {names}",
        "de": "Keine Kurvendaten für: {names}"},
    "axis_disp": {"en": "Displacement (µm)", "de": "Weg (µm)"},
    "axis_curve_stress": {"en": "Stress (MPa)", "de": "Spannung (MPa)"},
    "expander_title": {"en": "Group membership and underlying rows",
        "de": "Gruppenzugehörigkeit und zugrunde liegende Werte"},
    "summary_heading": {"en": "Key properties by group", "de": "Kennwerte je Gruppe"},
    "summary_caption": {
        "en": "All six properties per group, from each specimen's last step, "
              "averaged over the group.",
        "de": "Alle sechs Kennwerte je Gruppe, aus der letzten Stufe jeder Probe, "
              "über die Gruppe gemittelt."},
    "col_group": {"en": "Group", "de": "Gruppe"},
    "col_material": {"en": "Material", "de": "Material"},
    "col_specimen": {"en": "Specimen", "de": "Probe"},
    "col_n": {"en": "n", "de": "n"},
}


def _default_group_count(n_materials: int) -> int:
    return max(2, min(n_materials, MAX_GROUPS))


def _decimals(fmt: str) -> int:
    return len(fmt.split(".")[1]) if "." in fmt else 0


def _metric_label(col, lang: str) -> str:
    return column_label_de(col) if lang == "de" else col.label


def _headline_summary_table(
    df: pd.DataFrame, groups: list[dict], has_strain: bool, L, lang: str = "en",
) -> pd.DataFrame:
    """One row per group, every headline metric side by side, each specimen
    contributing its OWN last cycle (final state) before the group's
    specimens are averaged together -- so a group of one specimen is just
    that specimen's own values, and a multi-specimen group is not thrown
    off by specimens with different cycle counts (each is read at ITS OWN
    end, not a shared cycle number that might not exist for all of them).

    Deliberately the six headline metrics only (schema.HEADLINE_CYCLE_KEYS)
    -- the same set the Cycles-sheet Excel export and this page's own
    Metric dropdown are trimmed to; this table is not a way to bring back
    the supporting/detail columns that were removed everywhere else.
    """
    headline = [c for c in user_facing_cycle_columns(has_strain) if c.key != "Cycle"]
    rows: list[dict] = []
    for g in groups:
        sub = df[df["specimen_id"].isin(g["ids"])] if "specimen_id" in df.columns else df
        if sub.empty:
            continue
        last = sub.loc[sub.groupby("specimen_id")["Cycle"].idxmax()]
        row: dict = {L("col_group"): g["name"], L("col_n"): len(last)}
        for col in headline:
            vals = pd.to_numeric(last[col.key], errors="coerce").dropna()
            header = _metric_label(col, lang) + (f" ({col.unit})" if col.unit else "")
            row[header] = round(float(vals.mean()), _decimals(col.fmt)) if not vals.empty else None
        rows.append(row)
    return pd.DataFrame(rows)


def _stress_level(peaks: pd.Series) -> pd.Series:
    """Each cycle's peak stress rounded to STRESS_LEVEL_STEP_MPA: the stress
    step it belongs to on the chart's x axis."""
    p = pd.to_numeric(peaks, errors="coerce")
    return (p / STRESS_LEVEL_STEP_MPA).round() * STRESS_LEVEL_STEP_MPA


def _metric_by_stress(df: pd.DataFrame, groups: list[dict], key: str) -> pd.DataFrame:
    """Mean, SD and count of `key` per group and stress step. A group of one
    specimen has SD 0, so it shows its own value with no spread."""
    rows = []
    for g in groups:
        sub = df[df["specimen_id"].isin(g["ids"])]
        for level, part in sub.groupby("StressLevel_MPa"):
            vals = pd.to_numeric(part[key], errors="coerce").dropna()
            if vals.empty:
                continue
            sd = float(vals.std(ddof=0)) if len(vals) > 1 else 0.0
            rows.append({
                "group": g["name"], "stress": float(level), "mean": float(vals.mean()),
                "sd": sd, "n": len(vals),
            })
    agg = pd.DataFrame(rows)
    if not agg.empty:
        agg["err_lo"] = agg["mean"] - agg["sd"]
        agg["err_hi"] = agg["mean"] + agg["sd"]
    return agg


def _subtitle(unit: str, show_spread: bool, lang: str) -> str:
    """Unit plus what the points are, so a downloaded PNG explains itself."""
    what = {"en": "mean per stress step", "de": "Mittelwert je Laststufe"}[lang]
    if show_spread:
        what += {"en": " ± 1 SD", "de": " ± 1 SD"}[lang]
    return f"{unit} · {what}" if unit else what


def _curve_rows(
    ws: Workspace, specimens: pd.DataFrame, groups: list[dict], cycles: pd.DataFrame,
    level: float | None,
) -> tuple[pd.DataFrame, list[str]]:
    """Raw stress-displacement points of every specimen in every group, from
    the curve cache written at ingest (the same data Results draws). `level`
    keeps only the cycles of that stress step; None keeps all of them."""
    row_by_id = specimens.set_index("specimen_id")
    level_by_cycle = {
        (sid, int(c)): lv for sid, c, lv in
        cycles[["specimen_id", "Cycle", "StressLevel_MPa"]].itertuples(index=False)
    }
    frames, missing = [], []
    for g in groups:
        for sid in g["ids"]:
            row = row_by_id.loc[sid]
            path = curve_cache_path_for(ws.root / row["json_path"])
            try:
                cache = read_curve_cache(path)
            except (FileNotFoundError, OSError, ValueError):
                missing.append(str(row["label"]))
                continue
            tag = short_tag(str(row["label"]), 0)
            for cyc in cache.get("cycles", []):
                n = int(cyc.get("cycle", 0))
                lv = level_by_cycle.get((sid, n))
                if level is not None and lv != level:
                    continue
                pts = cyc.get("points") or []
                if not pts:
                    continue
                part = pd.DataFrame(pts, columns=["disp_mm", "stress"])
                part["disp_um"] = part["disp_mm"] * 1000.0
                part["order"] = range(len(part))
                part["group"] = g["name"]
                part["specimen"] = f"{row['material']} {tag}"
                part["line"] = f"{g['name']}|{sid}|{n}"
                part["step"] = lv
                frames.append(part)
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return data, missing


def _render_curves(ws, specimens, groups, cycles, group_order, color, L, lang) -> None:
    # No separate section heading: the chart carries the title itself, so a
    # downloaded PNG is labelled too.
    levels = sorted(cycles["StressLevel_MPa"].dropna().unique().tolist())
    options = [None] + levels
    level = st.selectbox(
        L("curves_step"), options, key="cmp_curve_step", index=0,
        format_func=lambda v: L("curves_all_steps") if v is None else L("curves_step_option", mpa=v),
    )
    data, missing = _curve_rows(ws, specimens, groups, cycles, level)
    if missing:
        st.caption(L("curves_missing", names=", ".join(missing)))
    if data.empty:
        return
    chart = (
        alt.Chart(data)
        .mark_line(strokeWidth=1.3, opacity=0.85)
        .encode(
            x=alt.X("disp_um:Q", title=L("axis_disp"), scale=alt.Scale(zero=False)),
            y=alt.Y("stress:Q", title=L("axis_curve_stress")),
            color=color, detail="line:N", order="order:Q",
            tooltip=[
                alt.Tooltip("group:N", title=L("tooltip_group")),
                alt.Tooltip("specimen:N", title=L("col_specimen")),
                alt.Tooltip("step:Q", title=L("tooltip_stress"), format=".0f"),
            ],
        )
        .properties(
            width=820, height=460,
            title=alt.TitleParams(
                text=L("curves_heading"),
                subtitle=L("curves_all_steps") if level is None else L("curves_step_option", mpa=level),
                fontSize=16, fontWeight=600, anchor="start", subtitleFontSize=12, offset=16,
            ),
        )
        .configure_view(stroke=None)
        .configure_axis(labelFontSize=12, titleFontSize=13)
        .configure_legend(labelFontSize=12, titleFontSize=13, symbolSize=110)
    )
    st.altair_chart(chart, use_container_width=False)
    st.caption(L("curves_caption"))


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
        L("metric_label"), options, format_func=lambda c: f"{_metric_label(c, lang)} ({c.unit})" if c.unit else _metric_label(c, lang),
    )

    df["StressLevel_MPa"] = _stress_level(df["PeakStress_MPa"])
    agg = _metric_by_stress(df, groups, metric.key)
    if agg.empty:
        st.info(L("no_values", label=_metric_label(metric, lang)))
        return

    show_spread = st.checkbox(
        L("spread_checkbox"), value=True, help=L("spread_help"),
    )

    group_order = [g["name"] for g in groups]
    metric_name = _metric_label(metric, lang)
    y_title = metric_name + (f" ({metric.unit})" if metric.unit else "")
    tooltip = [
        alt.Tooltip("group:N", title=L("tooltip_group")),
        alt.Tooltip("stress:Q", title=L("tooltip_stress"), format=".0f"),
        alt.Tooltip("mean:Q", title=metric_name, format=",.4~g"),
        alt.Tooltip("sd:Q", title=L("tooltip_sd"), format=",.3~g"),
        alt.Tooltip("n:Q", title=L("tooltip_specimens")),
    ]
    # Same fixed categorical order the Results dashboard uses -- a group's
    # colour follows its position in the form above, not a value sort, so
    # editing one group's specimens never repaints another group's line.
    color = alt.Color(
        "group:N", title=L("tooltip_group"), sort=group_order,
        scale=alt.Scale(domain=group_order, range=CATEGORICAL_LIGHT[: len(group_order)]),
    )
    # Stress on the x axis rather than the cycle number: a shared picture
    # then says "at 300 MPa" instead of "cycle 6", and runs with a different
    # number of steps (PL-0087 Case A and B) line up at the same stresses.
    x_stress = alt.X(
        "stress:Q", title=L("axis_stress"),
        scale=alt.Scale(domainMin=0, nice=True), axis=alt.Axis(tickMinStep=50),
    )

    base = alt.Chart(agg)
    layers = [
        base.mark_line(strokeWidth=2.2, point=alt.OverlayMarkDef(size=60, filled=True)).encode(
            x=x_stress,
            # Axis titled with the unit only: the chart title already names
            # the metric, so the full name twice just crowds the left edge.
            y=alt.Y("mean:Q", title=metric.unit or metric_name,
                    axis=alt.Axis(grid=True, format=",.4~g")),
            color=color, tooltip=tooltip,
        )
    ]
    if show_spread:
        layers.append(
            base.mark_rule(strokeWidth=1.6, opacity=0.7).encode(
                x="stress:Q", y="err_lo:Q", y2="err_hi:Q", color=color, tooltip=tooltip,
            )
        )
    # width is FIXED, not use_container_width=True: Vega-Lite text is set in
    # absolute pixels, so a screen-wide chart exports as a huge PNG with
    # comparatively tiny text. A fixed, moderate width keeps the text size in
    # proportion at every size the chart is shown or downloaded at.
    chart = (
        alt.layer(*layers)
        .properties(
            width=820, height=460,
            title=alt.TitleParams(
                text=metric_name, subtitle=_subtitle(metric.unit, show_spread, lang),
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

    _render_curves(ws, specimens, groups, df, group_order, color, L, lang)

    st.markdown(f"##### {L('summary_heading')}")
    st.caption(L("summary_caption"))
    st.dataframe(
        _headline_summary_table(df, groups, has_strain, L, lang),
        use_container_width=True, hide_index=True,
    )

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
            df[["material", "label", "Cycle", "StressLevel_MPa", metric.key]].rename(columns={
                "material": L("col_material"), "label": L("col_specimen"),
                "Cycle": L("tooltip_cycle"), "StressLevel_MPa": L("tooltip_stress"),
                metric.key: y_title,
            }),
            use_container_width=True, hide_index=True,
        )
