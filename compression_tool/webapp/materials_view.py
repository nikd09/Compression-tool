"""Materials: a library view of what exists in this workspace -- one card
per material, with the properties that matter when scanning the whole set,
plus a search box and a click-through into that material's full combined
dashboard (material_export.py's reports/<material>.html, embedded exactly
as Results embeds its own dashboard). Deliberately no chart of its own --
Results and Compare already own the deep dive, per material and across
materials respectively; this tab is just the index and the door into each
material's real charts."""

from __future__ import annotations

import html

import streamlit as st
import streamlit.components.v1 as components

from .. import permissions
from ..material_admin import delete_material, rename_material
from ..material_export import export_material
from ..persistence import Workspace, slugify
from ..reports_overview import material_rows
from .common import dashboard_lang, inject_dashboard_lang

# EN/DE strings for this page's own chrome -- not the embedded dashboard
# (results_dashboard.html), which is already translated on its own and
# just gets its starting language seeded here via inject_dashboard_lang().
_T = {
    "header": {"en": "Materials", "de": "Materialien"},
    "search_placeholder": {"en": "Search materials…", "de": "Materialien durchsuchen…"},
    "caption": {"en": "Every material in this workspace, at a glance. Click one to open its full dashboard.",
        "de": "Jedes Material in diesem Workspace auf einen Blick. Anklicken, um sein vollständiges Dashboard zu öffnen."},
    "no_data": {"en": "Nothing ingested into this workspace yet - use Ingest first.",
        "de": "In diesen Workspace wurde noch nichts eingelesen - zuerst unter „Ingest“ eine Datei hinzufügen."},
    "no_match": {"en": "No materials match “{query}”.", "de": "Keine Materialien passen zu „{query}“."},
    "specimens": {"en": "Specimens", "de": "Proben"},
    "runs": {"en": "Runs", "de": "Läufe"},
    "peak_stress": {"en": "Peak stress", "de": "Spitzenspannung"},
    "thickness": {"en": "Thickness (h0)", "de": "Dicke (h0)"},
    "rename": {"en": "Rename", "de": "Umbenennen"},
    "delete": {"en": "Delete", "de": "Löschen"},
    "download_dashboard": {"en": "Download dashboard", "de": "Dashboard herunterladen"},
    "dashboard_not_built": {"en": "Dashboard not built yet - click the material name above to open (and generate) it first.",
        "de": "Dashboard noch nicht erstellt - zuerst oben auf den Materialnamen klicken, um es zu öffnen (und zu erzeugen)."},
    "not_admin": {"en": "Only an admin can do this - see the \"Admin access\" card on the "
        "Config tab to see who is listed, or to claim admin access yourself if nobody has yet.",
        "de": "Das kann nur ein Admin. Auf der Karte „Admin-Zugriff“ im Config-Tab steht, wer "
        "gelistet ist, oder dort selbst Admin-Zugriff beanspruchen, falls es noch niemand hat."},
    "rename_dialog_title": {"en": "Rename material", "de": "Material umbenennen"},
    "rename_dialog_caption": {
        "en": "Renames \"{material}\" everywhere it appears: every run folder, "
              "specimen record, curve cache and report. Materials, Compare and "
              "Results all pick up the new name immediately - nothing needs "
              "re-ingesting.",
        "de": "Benennt „{material}“ überall um, wo es vorkommt: jeden Lauf-Ordner, "
              "jeden Probendatensatz, jeden Kurven-Cache und jeden Bericht. "
              "Materials, Compare und Results übernehmen den neuen Namen sofort - "
              "nichts muss neu eingelesen werden."},
    "new_name": {"en": "New name", "de": "Neuer Name"},
    "name_empty": {"en": "New name cannot be empty.", "de": "Der neue Name darf nicht leer sein."},
    "name_unchanged": {"en": "That's already the current name.", "de": "Das ist bereits der aktuelle Name."},
    "renamed_partial": {
        "en": "Renamed to '{material}', but {n} run folder(s) could not be moved "
              "on disk (records were still updated, so they show correctly here "
              "- only the folder name on disk still reads old): {failed}",
        "de": "Umbenannt in „{material}“, aber {n} Lauf-Ordner konnten auf der "
              "Festplatte nicht verschoben werden (die Datensätze wurden trotzdem "
              "aktualisiert und werden hier korrekt angezeigt - nur der "
              "Ordnername auf der Festplatte lautet noch alt): {failed}"},
    "renamed": {"en": "Renamed to '{material}'.", "de": "Umbenannt in „{material}“."},
    "cancel": {"en": "Cancel", "de": "Abbrechen"},
    "delete_dialog_title": {"en": "Delete material", "de": "Material löschen"},
    "delete_dialog_error": {
        "en": "This permanently deletes every run, specimen record, curve cache "
              "and report for \"{material}\". This cannot be undone from inside the app.",
        "de": "Dies löscht dauerhaft jeden Lauf, jeden Probendatensatz, jeden "
              "Kurven-Cache und jeden Bericht für „{material}“. Das kann in der "
              "App nicht rückgängig gemacht werden."},
    "delete_raw_checkbox": {"en": "Also delete its archived raw exports", "de": "Auch die archivierten Roh-Exporte löschen"},
    "delete_raw_help": {
        "en": "Off by default: a raw export is content-addressed and can be "
              "shared with another material's specimens - the same file ingested "
              "a second time under a different name reuses the identical archived "
              "copy. Left unchecked, only this material's own records, curve "
              "caches and reports are removed; a raw file still used elsewhere is "
              "never deleted even if this is checked.",
        "de": "Standardmäßig aus: ein Roh-Export ist inhaltsadressiert und kann "
              "mit Proben eines anderen Materials geteilt werden - dieselbe Datei, "
              "ein zweites Mal unter anderem Namen eingelesen, verwendet dieselbe "
              "archivierte Kopie erneut. Unmarkiert werden nur die eigenen "
              "Datensätze, Kurven-Caches und Berichte dieses Materials entfernt; "
              "eine anderswo noch verwendete Rohdatei wird nie gelöscht, selbst "
              "wenn dies markiert ist."},
    "confirm_type": {"en": "Type \"{material}\" to confirm", "de": "„{material}“ eingeben, um zu bestätigen"},
    "delete_permanently": {"en": "Delete permanently", "de": "Dauerhaft löschen"},
    "deleted_toast": {"en": "Deleted {specimens} specimen(s) across {runs} run(s).",
        "de": "{specimens} Probe(n) über {runs} Lauf/Läufe gelöscht."},
    "back_to_materials": {"en": "← Back to Materials", "de": "← Zurück zu Materialien"},
    "no_dashboard": {"en": "No dashboard could be built for {material!r}: it may have no indexed specimens.",
        "de": "Für {material!r} konnte kein Dashboard erstellt werden: es hat möglicherweise keine indizierten Proben."},
}

# Rename/Delete are visible to every visitor, not hidden for a non-admin --
# someone who cannot use them should still be able to see the option exists
# and find out why it is blocked (this message, shown on click), rather than
# a feature that only admins even know is there. permissions.is_admin() is
# checked at CLICK time instead, inside render() below.

# Scoped via st.container(key=...) -- a documented, stable Streamlit hook for
# exactly this (style one specific container's contents without a fragile
# sibling-selector trick) -- so this cannot leak onto a tertiary button, or a
# bordered container, anywhere else the app might add one later.
#
# Cards are targeted by [class*="st-key-mat_card_"], not
# [data-testid="stVerticalBlockBorderWrapper"] -- that testid, which the
# rest of this app's hover rule (webapp/common.py) still uses, no longer
# exists in the installed Streamlit version (confirmed live: zero matches
# anywhere in the rendered DOM, on every tab). border=True now applies
# directly to the stVerticalBlock itself; giving each card its own key is
# what makes it addressable at all without relying on the auto-generated,
# version-tied emotion-cache class name every bordered container happens to
# share.
_CARD_CSS = """
<style>
/* A responsive grid, not a stacked full-width column -- as many cards per
   row as actually fit (auto-fill), each a comfortable roughly-square card
   rather than a thin full-width strip. Applied straight to the same
   element that already carries the st-key-materials_grid class: it is the
   outer stVerticalBlock, and each card -- one st.container(border=True,
   key=...) per material -- is already its direct child in the DOM, so
   nothing about how the cards are built in Python has to change for them
   to become grid items instead of stacked rows. */
.st-key-materials_grid{
  display:grid!important;
  /* 230px is chosen so four equal-width cards fit at ~960px of content
     width and up -- confirmed live at a 1440px browser window with the
     sidebar open; the 300px this replaced only ever reached three columns
     at that size. auto-fill still grows to five, six... on a bigger
     monitor and drops to three, two, one on a narrower one, every card
     always the same width as its row-mates (1fr) at whatever size
     actually fits. Rename/Delete/Download are stacked full-width buttons
     (not side by side) specifically so this width never wraps their text
     -- see the three separate st.button/st.download_button calls below,
     each use_container_width=True on its own row. */
  grid-template-columns:repeat(auto-fill,minmax(230px,1fr))!important;
  gap:1rem!important;
  /* Grid items stretch to the row's tallest by default -- EXCEPT Streamlit's
     own base styling sets align-items:start upstream of this rule (confirmed
     live via computed styles: a 3-line material name's card measured 414px
     while its unwrapped row-mates measured 360px, the row's own tallest
     item never being shared). This is the actual fix; the flex rules below
     are what make that extra height land somewhere useful once each card
     has it, instead of just adding blank space below a short title. */
  align-items:stretch!important;
}
.st-key-materials_grid button[kind="tertiary"]{
  padding:0!important; border:none!important; background:transparent!important;
  box-shadow:none!important; justify-content:flex-start!important;
  min-height:0!important;
}
.st-key-materials_grid button[kind="tertiary"] p{
  font-size:1.05rem!important; font-weight:650!important; letter-spacing:-.012em!important;
  color:var(--text-color,inherit)!important;
}
.st-key-materials_grid button[kind="tertiary"]:hover p{ color:var(--primary-color,#2a78d6)!important; }

.st-key-materials_grid [class*="st-key-mat_card_"]{
  padding:.85rem 1rem!important;
  transition:box-shadow .18s ease, transform .18s ease, border-color .18s ease;
  /* height:100% fills the now-stretched grid cell (see align-items:stretch
     above); the flex column is what lets mat_top_ (below) claim the extra
     room a shorter card's row-mate has, rather than every child just
     sitting at its own natural height with dead space at the bottom. */
  height:100%!important;
  display:flex!important;
  flex-direction:column!important;
}
.st-key-materials_grid [class*="st-key-mat_card_"]:hover{
  box-shadow:0 8px 22px rgba(0,0,0,.10);
  transform:translateY(-2px);
  border-color:var(--primary-color,#2a78d6);
}
/* Everything above the button stack (name, date, the four metrics) grows
   to absorb the row's extra height -- a two- or three-line material name
   on one card no longer leaves that card's own Rename/Delete/Download
   buttons sitting lower than every other card in the same row; they stay
   pinned to the bottom of every card, at the same height, across the row. */
.st-key-materials_grid [class*="st-key-mat_top_"]{
  flex:1 1 auto!important;
}
.st-key-materials_grid [data-testid="stMetricValue"]{ font-size:1.3rem!important; }
.st-key-materials_grid [data-testid="stMetricLabel"]{ font-size:.66rem!important; }
@media (prefers-color-scheme: dark){
  .st-key-materials_grid button[kind="tertiary"]:hover p{ color:var(--primary-color,#3987e5)!important; }
  .st-key-materials_grid [class*="st-key-mat_card_"]:hover{ border-color:var(--primary-color,#3987e5); }
}
</style>
"""

# A comfortable, fixed viewport for the embedded dashboard -- same tuning as
# results_view.py's own _FRAME_HEIGHT_PX, for the same template (a normal
# scrollable box the template's own `vh`-sized expanded-chart dialog can
# measure correctly, rather than a content-fit guess).
_FRAME_HEIGHT_PX = 820

_SESSION_KEY = "materials_open"


def render(ws: Workspace) -> None:
    lang = dashboard_lang()

    def L(key: str, **kw) -> str:
        s = _T[key][lang]
        return s.format(**kw) if kw else s

    selected = st.session_state.get(_SESSION_KEY)
    if selected:
        _render_material_dashboard(ws, selected, L)
        return

    head_l, head_r = st.columns([3.2, 1.3])
    with head_l:
        st.header(L("header"))
    with head_r:
        st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        query = st.text_input(
            "Search", placeholder=L("search_placeholder"), label_visibility="collapsed",
        )
    st.caption(L("caption"))

    rows = material_rows(ws)
    if not rows:
        st.info(L("no_data"))
        return

    if query.strip():
        q = query.strip().casefold()
        rows = [r for r in rows if q in r["material"].casefold()]
    if not rows:
        st.info(L("no_match", query=query))
        return

    can_manage = permissions.is_admin(ws)
    st.markdown(_CARD_CSS, unsafe_allow_html=True)
    clicked_material = None
    with st.container(key="materials_grid"):
        for row in rows:
            with st.container(border=True, key=f"mat_card_{row['slug']}"):
                # Everything above the button stack, in its own container
                # (key "mat_top_..." -- deliberately NOT sharing the
                # "mat_card_" prefix _CARD_CSS's border/hover rules match on
                # a substring, or this inner wrapper would pick up the
                # outer card's own border/hover treatment too). Grid rows
                # stretch every card to the tallest one in that row (see
                # align-items:stretch below), and this is what actually
                # absorbs the extra height when a longer material name
                # wraps to two or three lines -- without it, only the
                # wrapping card grows while its row-mates stay their own
                # shorter content height, so their button stacks end up at
                # a different vertical position across the same row.
                with st.container(key=f"mat_top_{row['slug']}"):
                    name_col, added_col = st.columns([5, 2])
                    with name_col:
                        if st.button(row["material"], key=f"open_material_{row['slug']}", type="tertiary"):
                            clicked_material = row["material"]
                    with added_col:
                        added = row["dateAdded"]
                        added = added[:10] if len(added) >= 10 else added
                        # No "Added " prefix and white-space:nowrap -- with the
                        # label there, "Added 2026-08-26" wrapped inside this
                        # narrow a column (a grid cell now, not a full-width row),
                        # dropping the date onto its own second line. The date
                        # alone, forced onto one line, is what actually fits.
                        st.markdown(
                            f'<div style="text-align:right;font-size:.72rem;opacity:.6;'
                            f'margin-top:.55rem;white-space:nowrap">{html.escape(added)}</div>',
                            unsafe_allow_html=True,
                        )
                    # 2x2, not a single row of 4: a card this narrow (a grid
                    # cell, not a full-width row any more) has no room for four
                    # metric columns side by side without truncating values
                    # like "0.471 mm".
                    top_l, top_r = st.columns(2)
                    top_l.metric(L("specimens"), row["specimens"])
                    top_r.metric(L("runs"), row["runs"])
                    bot_l, bot_r = st.columns(2)
                    bot_l.metric(
                        L("peak_stress"),
                        f"{row['meanPeak']:.0f} MPa" if row["meanPeak"] is not None else "-",
                    )
                    bot_r.metric(
                        L("thickness"),
                        f"{row['meanH0']:.3f} mm" if row["meanH0"] is not None else "-",
                    )
                # Stacked full-width, not side by side: at four-per-row
                # card widths, "Rename"/"Delete" in a 2-column row wrapped
                # onto two lines each (confirmed live) -- one button per
                # row never wraps regardless of how narrow the card gets.
                if st.button(
                    L("rename"), key=f"rename_material_{row['slug']}",
                    icon=":material/edit:", use_container_width=True,
                ):
                    if can_manage:
                        st.dialog(L("rename_dialog_title"))(_rename_dialog_body)(ws, row["material"], L)
                    else:
                        st.error(L("not_admin"))
                if st.button(
                    L("delete"), key=f"delete_material_{row['slug']}",
                    icon=":material/delete:", use_container_width=True,
                ):
                    if can_manage:
                        st.dialog(L("delete_dialog_title"))(_delete_dialog_body)(ws, row["material"], L)
                    else:
                        st.error(L("not_admin"))

                # Downloading a local copy of the dashboard needs no admin
                # access -- it is a read, not a write, the same as opening
                # the material's dashboard by clicking its card. Only shown
                # once the combined dashboard actually exists on disk:
                # building it from here (export_material(), the same slow
                # path _render_material_dashboard() falls back to) would
                # mean every card in the grid pays that cost on every
                # rerun, not just the one someone actually wants.
                dashboard_path = ws.root / "reports" / f"{slugify(row['material'])}.html"
                if dashboard_path.exists():
                    st.download_button(
                        L("download_dashboard"), data=dashboard_path.read_bytes(),
                        file_name=f"{row['material']}.html", mime="text/html",
                        key=f"download_material_{row['slug']}",
                        icon=":material/download:", use_container_width=True,
                    )
                else:
                    st.caption(L("dashboard_not_built"))
    if clicked_material:
        st.session_state[_SESSION_KEY] = clicked_material
        # Plain state, not a widget key -- results_view.py's Material
        # selectbox is the only place this is ever bound to a widget, so
        # writing it here just carries the choice forward, the same
        # cross-page default clicking a card already gives you within this
        # tab (see _SESSION_KEY above).
        st.session_state["active_material"] = clicked_material
        st.rerun()


def _rename_dialog_body(ws: Workspace, material: str, L) -> None:
    st.caption(L("rename_dialog_caption", material=material))
    new_name = st.text_input(L("new_name"), value=material)
    c1, c2 = st.columns(2)
    with c1:
        if st.button(L("rename"), type="primary", use_container_width=True):
            if not new_name.strip():
                st.error(L("name_empty"))
            elif new_name.strip() == material:
                st.info(L("name_unchanged"))
            else:
                try:
                    result = rename_material(ws, material, new_name.strip())
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    if result["failed"]:
                        # st.toast, not st.warning/st.success below: this
                        # dialog closes via st.rerun() right after, which
                        # would otherwise discard the message before it was
                        # ever seen -- a toast is the one Streamlit element
                        # that survives past the rerun that triggers it.
                        st.toast(
                            L(
                                "renamed_partial", material=result["material"],
                                n=len(result["failed"]), failed=", ".join(result["failed"]),
                            ),
                            icon=":material/warning:",
                        )
                    else:
                        st.toast(L("renamed", material=result["material"]), icon=":material/check:")
                    st.rerun()
    with c2:
        if st.button(L("cancel"), use_container_width=True):
            st.rerun()


def _delete_dialog_body(ws: Workspace, material: str, L) -> None:
    st.error(L("delete_dialog_error", material=material))
    delete_raw = st.checkbox(L("delete_raw_checkbox"), help=L("delete_raw_help"))
    confirm = st.text_input(L("confirm_type", material=material))
    c1, c2 = st.columns(2)
    with c1:
        if st.button(
            L("delete_permanently"), type="primary", use_container_width=True,
            disabled=(confirm != material),
        ):
            try:
                result = delete_material(ws, material, delete_raw=delete_raw)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop(_SESSION_KEY, None)
                st.toast(
                    L("deleted_toast", specimens=result["removed_specimens"], runs=result["removed_run_dirs"]),
                    icon=":material/check:",
                )
                st.rerun()
    with c2:
        if st.button(L("cancel"), use_container_width=True):
            st.rerun()


def _render_material_dashboard(ws: Workspace, material: str, L) -> None:
    if st.button(L("back_to_materials"), icon=":material/arrow_back:"):
        del st.session_state[_SESSION_KEY]
        st.rerun()

    # Read the already-built combined dashboard rather than rebuild it from
    # a picked subset (Results does that): this is the SAME file colleagues
    # open directly from the shared drive, every specimen ever ingested for
    # the material, so what opens here never disagrees with what opens there.
    html_path = ws.root / "reports" / f"{slugify(material)}.html"
    if not html_path.exists():
        exported = export_material(ws, material)
        html_path = exported["html"]
    if not html_path or not html_path.exists():
        st.warning(L("no_dashboard", material=material))
        return

    st.subheader(material)
    components.html(
        inject_dashboard_lang(html_path.read_text(encoding="utf-8")),
        height=_FRAME_HEIGHT_PX, scrolling=True
    )
