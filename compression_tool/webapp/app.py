"""Entry point.

    streamlit run compression_tool/webapp/app.py

Absolute imports only, deliberately: Streamlit's script runner execs this
file directly (even under `streamlit run -m`) without setting `__package__`,
so `from . import ...` fails here even though it works everywhere else in
the package. `compression_tool` just needs to be importable -- installed, or
this repo's root on PYTHONPATH.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from compression_tool.logging_config import configure_logging
from compression_tool.persistence import default_index_root
from compression_tool.webapp import (
    compare_view,
    config_view,
    ingest_view,
    materials_view,
    overview_view,
    results_view,
)
from compression_tool.webapp.auth import require_password
from compression_tool.webapp.common import language_picker, polish, workspace_picker

st.set_page_config(page_title="CompressLab", page_icon="📊", layout="wide")

# Same per-machine local folder default_index_root() already uses for
# knowledge_base.db, for the same reason -- see logging_config.py.
configure_logging(default_index_root() / "logs")

_STATIC = Path(__file__).parent / "static"

# Deliberately NOT st.navigation/st.Page: its URL-path routing clears
# st.session_state on every page switch in this environment (confirmed with
# an isolated repro -- session_state comes back {} on the destination page,
# not just one widget). A single script with the "current view" held in
# session_state and dispatched manually has no page navigation to lose state
# across -- it is one script run, same as it always was.
#
# That alone was not enough. A second bug survived it: the Workspace input
# still reset on every nav click, even called from this one site. Root cause,
# confirmed with an isolated repro: the nav buttons called st.rerun()
# immediately on click, INSIDE their loop -- which halts the script right
# there, before it ever reaches the text_input a few lines below. Streamlit
# garbage-collects session_state for any keyed widget that was not
# instantiated on the run that just completed, so the workspace value was
# being cleared on the very run that changed the page, before the widget that
# owned it had a chance to run again. A button click already triggers
# Streamlit's normal rerun on its own; the explicit st.rerun() was not only
# unnecessary, it was actively cutting the script short. Removing it lets
# every run reach every widget, every time.
#
# Also architectural, and still worth keeping: the Workspace input is
# rendered ONCE, here, and the resolved Workspace is passed into every view as
# a plain argument, rather than each view re-declaring its own copy of the
# widget -- see common.workspace_picker's docstring.
# Grouped into sections, not one flat list: the five-then-six original
# items read as "the six Python files", not as the underlying tasks a
# materials engineer actually performs. Workflow is the pipeline for one
# test (get oriented, bring in data, understand a result, compare results);
# Library is the catalogue that pipeline builds up over time; System is
# workspace-level administration that most visits never touch. The section
# label is cosmetic (a small caption, not a real grouping construct
# Streamlit has) but the grouping itself is what the nav is missing, not
# just a visual label -- see NAV_ITEMS below, still the one flat lookup
# every button click and view dispatch actually uses.
NAV_SECTIONS = [
    ("Workflow", [
        ("Ingest", ":material/upload_file:", ingest_view.render),
        ("Results", ":material/bar_chart:", results_view.render),
        ("Compare", ":material/compare_arrows:", compare_view.render),
        ("Overview", ":material/space_dashboard:", overview_view.render),
    ]),
    ("Library", [
        ("Materials", ":material/inventory_2:", materials_view.render),
    ]),
    ("System", [
        ("Config", ":material/tune:", config_view.render),
    ]),
]
NAV_ITEMS = [item for _, items in NAV_SECTIONS for item in items]
# The landing page on first load -- pinned by name, not by NAV_ITEMS[0], so
# Overview's own position within Workflow (see above) can move without
# silently changing what a fresh session opens on. Ingest, not Overview: a
# fresh session most often means "bring in a new test", and Overview is one
# click away in the same section for whoever wants the orientation view
# first.
_DEFAULT_NAV_VIEW = "Ingest"

# DISPLAYED text only -- the English names above stay the stable internal
# identity (session_state["nav_view"], the NAV_ITEMS dispatch key, the
# widget key, _DEFAULT_NAV_VIEW's own comparison), so switching language
# never resets which page is open or breaks a bookmarked nav_view value.
# See common.language_picker()'s docstring for the one shared toggle this
# reads from.
_SECTION_LABELS_DE = {"Workflow": "Arbeitsablauf", "Library": "Bibliothek", "System": "System"}
_NAV_LABELS_DE = {
    "Ingest": "Einlesen", "Results": "Ergebnisse", "Compare": "Vergleich",
    "Overview": "Übersicht", "Materials": "Materialien", "Config": "Konfiguration",
}

_NAV_CSS = """
<style>
  /* The sidebar header (the logo + the collapse chevron): flat, not the
     diagonal gradient band + corner glow this used to have -- the logo
     mark itself now carries its own colour and motion (see logo.svg), so
     the header around it stays quiet instead of adding a second, competing
     visual effect. A plain bottom border is still what separates it from
     the nav list below.

     The logo image is pinned to a fixed size instead of the max-width:100%
     Streamlit gives it by default -- not cosmetic: the sidebar itself is
     user-resizable by dragging its edge, and max-width:100% means the logo
     -- icon AND the "CompressLab" wordmark text baked into the same image
     -- visibly shrinks and gets harder to read as the sidebar narrows
     (confirmed live: dragging the sidebar from 300px to 200px shrank the
     rendered logo from 176px to 100px wide). Pinning the size means it
     clips instead of shrinking illegibly if the sidebar is dragged
     narrower than the logo itself -- the same trade-off most sidebars with
     a fixed brand mark make.

     A small negative margin on the header (not on stSidebarContent, which
     would also pull every nav button and the workspace box left with it)
     is what closes the left-edge gap Streamlit's own 20px sidebar padding
     otherwise leaves specifically in front of the logo -- confirmed live
     via computed styles that stSidebarContent's padding-left is the entire
     source of that gap, not something this header applies itself.
  */
  [data-testid="stSidebarHeader"]{
    position:relative; overflow:hidden;
    margin-left:-12px;
    border-bottom:1px solid var(--border-color, #e1e0d9);
  }
  [data-testid="stSidebarLogo"]{
    max-width:none!important; width:176px!important; height:32px!important;
  }
  @media (prefers-color-scheme: dark){
    [data-testid="stSidebarHeader"]{
      border-bottom-color:#2c2c2a;
    }
  }

  /* The nav row's own button -- flat by default, an accent bar and a
     slight rightward shift on hover, the same bar solid and already "in"
     for whichever page is active. The first rule this replaced targeted
     [data-testid="stVerticalBlockBorderWrapper"] wrapping the button,
     which -- like every other use of that testid in this app -- turned out
     to not exist anywhere in this Streamlit version's DOM (see README,
     "Materials cards hover"); it never matched anything, so removing it
     changes nothing about what actually painted before this. */
  section[data-testid="stSidebar"] div.stButton > button{
    position:relative; justify-content:flex-start; border:1px solid transparent;
    background:transparent; color:var(--text-color,inherit); font-weight:560;
    padding:.5rem .6rem .5rem .7rem; border-radius:.5rem; box-shadow:none;
    width:100%; transition:background-color .16s ease, padding-left .16s ease,
    color .16s ease;
  }
  section[data-testid="stSidebar"] div.stButton > button::before{
    content:""; position:absolute; left:-2px; top:20%; bottom:20%; width:3px;
    border-radius:2px; background:#2a78d6;
    transform:scaleY(0); transition:transform .18s ease;
  }
  section[data-testid="stSidebar"] div.stButton > button:hover{
    background:rgba(127,127,127,.10); border-color:transparent; padding-left:.95rem;
  }
  section[data-testid="stSidebar"] div.stButton > button:hover::before{
    transform:scaleY(.6);
  }
  section[data-testid="stSidebar"] div.stButton > button[kind="primary"]{
    background:rgba(42,120,214,.12); color:#2a78d6;
    border-color:transparent;
  }
  section[data-testid="stSidebar"] div.stButton > button[kind="primary"]::before{
    transform:scaleY(1);
  }
  section[data-testid="stSidebar"] div.stButton > button p{
    font-size:.92rem; transition:transform .16s ease;
  }
  section[data-testid="stSidebar"] div.stButton > button:hover p{
    transform:translateX(1px);
  }
  /* The sidebar's own TOP-LEVEL stack -- the language toggle, both
     st.divider() calls, the nav_menu block, and the workspace picker below
     it -- is a separate flex container from nav_menu's own internal one,
     laid out with Streamlit's default ~1rem gap PLUS each <hr>'s own
     ~1.5rem margin on top of that. The nav_menu rule below only ever
     reached the button list *inside* that one container, so the biggest
     gap on the page -- language toggle down to "WORKFLOW" -- was never
     touched by it (confirmed live: everything below the first divider
     tightened, that one gap didn't). Placed BEFORE the nav_menu rule so,
     at equal specificity with both !important, the later, more specific
     nav_menu rule still wins there and stays at its own tighter .1rem.
  */
  section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{
    gap:.4rem!important;
  }
  section[data-testid="stSidebar"] hr{
    margin:.4rem 0!important;
  }
  /* Tightens the nav list itself, scoped to the key="nav_menu" container
     around it in main() below -- Streamlit's own default packs every
     stacked element (button, divider, section caption alike) with about
     1rem of gap, which reads as visible empty air between five or six
     single-line menu entries. `display:flex` is set explicitly here, not
     assumed already present, so this `gap` is guaranteed to take effect
     regardless of which internal layout mechanism the installed Streamlit
     version otherwise uses for a plain block. */
  /* Matched on `section[data-testid="stSidebar"] [class*=...]`, not the
     bare class selector -- the general stVerticalBlock rule just above
     carries two attribute selectors, so at equal specificity + !important
     the LATER rule wins on source order alone; that briefly left this
     container at .4rem instead of .1rem (confirmed live: nav item spacing
     visibly grew back). Matching its selector shape keeps this one
     unambiguously the more specific of the two regardless of order. */
  section[data-testid="stSidebar"] [class*="st-key-nav_menu"]{
    display:flex!important; flex-direction:column!important; gap:.1rem!important;
  }
  /* Section captions above each nav group -- Workflow / Library / System --
     quiet enough not to compete with the buttons themselves, just enough
     of a label that the five items read as three grouped tasks rather than
     one flat list of source files. */
  .ct-nav-section{
    font-size:.68rem; font-weight:700; letter-spacing:.07em; text-transform:uppercase;
    opacity:.5; margin:.9rem 0 .15rem .7rem;
  }
  .ct-nav-section:first-child{ margin-top:.15rem; }
  /* [theme.dark]'s own brighter blue step, same pair as everywhere else
     in this file that reads var(--primary-color) -- see common.py. */
  @media (prefers-color-scheme: dark){
    section[data-testid="stSidebar"] div.stButton > button::before{
      background:#3987e5;
    }
    section[data-testid="stSidebar"] div.stButton > button[kind="primary"]{
      background:rgba(57,135,229,.18); color:#3987e5;
    }
  }
</style>
"""


def main() -> None:
    # First thing after set_page_config, before anything else renders --
    # see auth.py. A no-op unless COMPRESSION_TOOL_PASSWORD is set.
    require_password()
    st.logo(
        str(_STATIC / "logo.svg"), size="large",
        icon_image=str(_STATIC / "logo-icon.svg"),
    )
    polish()
    st.markdown(_NAV_CSS, unsafe_allow_html=True)
    st.session_state.setdefault("nav_view", _DEFAULT_NAV_VIEW)
    nav_before_this_run = st.session_state["nav_view"]

    with st.sidebar:
        # The ONE language toggle for the whole app -- see
        # common.language_picker()'s own docstring for why this replaced
        # Compare's separate radio and every dashboard's separate in-page
        # buttons defaulting independently. First thing in the sidebar,
        # same reasoning a language switcher is conventionally the first
        # thing on a page: it should be seen and set before anything else
        # here is read.
        nav_lang = language_picker()
        st.divider()
        # Scoped container (key="nav_menu") so the tightened-gap CSS below
        # only ever touches this one block -- Streamlit lays every direct
        # child of the sidebar's own top-level block out with one shared
        # `gap`, so without this boundary the same rule would also squeeze
        # the language toggle, both dividers and the workspace expander
        # below, not just the button list a tighter menu was actually
        # asked for.
        with st.container(key="nav_menu"):
            for section_name, items in NAV_SECTIONS:
                section_label = _SECTION_LABELS_DE[section_name] if nav_lang == "de" else section_name
                st.markdown(f'<div class="ct-nav-section">{section_label}</div>', unsafe_allow_html=True)
                for name, icon, _ in items:
                    # active is read BEFORE this button's own click is known, so
                    # the button just clicked always paints with its OLD state on
                    # this exact run -- a real, confirmed one-click highlight lag,
                    # not a hover artifact (reproduced with the mouse moved off
                    # the sidebar entirely). Content is correct immediately
                    # either way, since `view(ws)` below reads the
                    # already-updated session_state; only the sidebar's own paint
                    # of ITSELF lags. Fixed below.
                    active = st.session_state["nav_view"] == name
                    nav_label = _NAV_LABELS_DE[name] if nav_lang == "de" else name
                    if st.button(
                        nav_label, icon=icon, key=f"nav_{name}",
                        use_container_width=True,
                        type="primary" if active else "tertiary",
                    ):
                        # No st.rerun() here -- see the note above.
                        st.session_state["nav_view"] = name
        st.divider()
        # The one and only call site for this widget -- see the note above.
        ws = workspace_picker()

    view = {name: fn for name, _, fn in NAV_ITEMS}[st.session_state["nav_view"]]
    view(ws)

    # Cosmetic rerun for the sidebar's own highlight, placed as the LAST
    # statement -- after every widget in this run, including the just-clicked
    # page's own, has already been instantiated once. That is what makes this
    # one safe where the nav loop's old st.rerun() was not: nothing is left
    # un-rendered for Streamlit to garbage-collect state for. It only fires on
    # the run that actually changed pages, not on every rerun.
    if st.session_state["nav_view"] != nav_before_this_run:
        st.rerun()


if __name__ == "__main__":
    main()
