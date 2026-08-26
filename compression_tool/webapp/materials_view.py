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
  grid-template-columns:repeat(auto-fill,minmax(300px,1fr))!important;
  gap:1rem!important;
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
.st-key-materials_grid button[kind="tertiary"]:hover p{ color:var(--primary-color,#d6006e)!important; }

.st-key-materials_grid [class*="st-key-mat_card_"]{
  padding:.85rem 1rem!important;
  transition:box-shadow .18s ease, transform .18s ease, border-color .18s ease;
}
.st-key-materials_grid [class*="st-key-mat_card_"]:hover{
  box-shadow:0 8px 22px rgba(0,0,0,.10);
  transform:translateY(-2px);
  border-color:var(--primary-color,#d6006e);
}
.st-key-materials_grid [data-testid="stMetricValue"]{ font-size:1.3rem!important; }
.st-key-materials_grid [data-testid="stMetricLabel"]{ font-size:.66rem!important; }
@media (prefers-color-scheme: dark){
  .st-key-materials_grid button[kind="tertiary"]:hover p{ color:var(--primary-color,#e0227e)!important; }
  .st-key-materials_grid [class*="st-key-mat_card_"]:hover{ border-color:var(--primary-color,#e0227e); }
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
    selected = st.session_state.get(_SESSION_KEY)
    if selected:
        _render_material_dashboard(ws, selected)
        return

    head_l, head_r = st.columns([3.2, 1.3])
    with head_l:
        st.header("Materials")
    with head_r:
        st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        query = st.text_input(
            "Search", placeholder="Search materials…", label_visibility="collapsed",
        )
    st.caption("Every material in this workspace, at a glance. Click one to open its full dashboard.")

    rows = material_rows(ws)
    if not rows:
        st.info("Nothing ingested into this workspace yet - use Ingest first.")
        return

    if query.strip():
        q = query.strip().casefold()
        rows = [r for r in rows if q in r["material"].casefold()]
    if not rows:
        st.info(f"No materials match “{query}”.")
        return

    can_manage = permissions.is_admin(ws)
    st.markdown(_CARD_CSS, unsafe_allow_html=True)
    clicked_material = None
    with st.container(key="materials_grid"):
        for row in rows:
            with st.container(border=True, key=f"mat_card_{row['slug']}"):
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
                top_l.metric("Specimens", row["specimens"])
                top_r.metric("Runs", row["runs"])
                bot_l, bot_r = st.columns(2)
                bot_l.metric(
                    "Peak stress",
                    f"{row['meanPeak']:.0f} MPa" if row["meanPeak"] is not None else "-",
                )
                bot_r.metric(
                    "Thickness (h0)",
                    f"{row['meanH0']:.3f} mm" if row["meanH0"] is not None else "-",
                )
                if can_manage:
                    rn_col, del_col = st.columns(2)
                    with rn_col:
                        if st.button(
                            "Rename", key=f"rename_material_{row['slug']}",
                            icon=":material/edit:", use_container_width=True,
                        ):
                            _rename_dialog(ws, row["material"])
                    with del_col:
                        if st.button(
                            "Delete", key=f"delete_material_{row['slug']}",
                            icon=":material/delete:", use_container_width=True,
                        ):
                            _delete_dialog(ws, row["material"])
    if clicked_material:
        st.session_state[_SESSION_KEY] = clicked_material
        st.rerun()


@st.dialog("Rename material")
def _rename_dialog(ws: Workspace, material: str) -> None:
    st.caption(
        f"Renames \"{material}\" everywhere it appears: every run folder, "
        f"specimen record, curve cache and report. Materials, Compare and "
        f"Results all pick up the new name immediately - nothing needs "
        f"re-ingesting."
    )
    new_name = st.text_input("New name", value=material)
    c1, c2 = st.columns(2)
    with c1:
        if st.button("Rename", type="primary", use_container_width=True):
            if not new_name.strip():
                st.error("New name cannot be empty.")
            elif new_name.strip() == material:
                st.info("That's already the current name.")
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
                            f"Renamed to '{result['material']}', but "
                            f"{len(result['failed'])} run folder(s) could not "
                            f"be moved on disk (records were still updated, "
                            f"so they show correctly here - only the folder "
                            f"name on disk still reads old): "
                            + ", ".join(result["failed"]),
                            icon=":material/warning:",
                        )
                    else:
                        st.toast(f"Renamed to '{result['material']}'.", icon=":material/check:")
                    st.rerun()
    with c2:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


@st.dialog("Delete material")
def _delete_dialog(ws: Workspace, material: str) -> None:
    st.error(
        f"This permanently deletes every run, specimen record, curve cache "
        f"and report for \"{material}\". This cannot be undone from inside "
        f"the app."
    )
    delete_raw = st.checkbox(
        "Also delete its archived raw exports",
        help="Off by default: a raw export is content-addressed and can be "
        "shared with another material's specimens - the same file ingested "
        "a second time under a different name reuses the identical archived "
        "copy. Left unchecked, only this material's own records, curve "
        "caches and reports are removed; a raw file still used elsewhere is "
        "never deleted even if this is checked.",
    )
    confirm = st.text_input(f'Type "{material}" to confirm')
    c1, c2 = st.columns(2)
    with c1:
        if st.button(
            "Delete permanently", type="primary", use_container_width=True,
            disabled=(confirm != material),
        ):
            try:
                result = delete_material(ws, material, delete_raw=delete_raw)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop(_SESSION_KEY, None)
                st.toast(
                    f"Deleted {result['removed_specimens']} specimen(s) "
                    f"across {result['removed_run_dirs']} run(s).",
                    icon=":material/check:",
                )
                st.rerun()
    with c2:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


def _render_material_dashboard(ws: Workspace, material: str) -> None:
    if st.button("← Back to Materials", icon=":material/arrow_back:"):
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
        st.warning(f"No dashboard could be built for {material!r}: it may have no indexed specimens.")
        return

    st.subheader(material)
    components.html(
        html_path.read_text(encoding="utf-8"), height=_FRAME_HEIGHT_PX, scrolling=True
    )
