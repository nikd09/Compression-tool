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

from compression_tool.webapp import (
    compare_view,
    config_view,
    ingest_view,
    materials_view,
    results_view,
)
from compression_tool.webapp.common import polish, workspace_picker

st.set_page_config(page_title="Compression Tool", page_icon="📊", layout="wide")

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
NAV_ITEMS = [
    ("Ingest", ":material/upload_file:", ingest_view.render),
    ("Materials", ":material/inventory_2:", materials_view.render),
    ("Results", ":material/bar_chart:", results_view.render),
    ("Compare", ":material/compare_arrows:", compare_view.render),
    ("Config", ":material/tune:", config_view.render),
]

_NAV_CSS = """
<style>
  section[data-testid="stSidebar"] div[data-testid="stVerticalBlockBorderWrapper"]
    div.stButton > button{
    justify-content:flex-start; border:none; background:transparent;
    font-weight:560; padding:.4rem .6rem; border-radius:.5rem;
    box-shadow:none;
  }
  section[data-testid="stSidebar"] div.stButton > button{
    justify-content:flex-start; border:1px solid transparent; background:transparent;
    color:var(--text-color,inherit); font-weight:560; padding:.4rem .6rem;
    border-radius:.5rem; box-shadow:none; width:100%;
  }
  section[data-testid="stSidebar"] div.stButton > button:hover{
    background:rgba(127,127,127,.10); border-color:transparent;
  }
  section[data-testid="stSidebar"] div.stButton > button[kind="primary"]{
    background:rgba(42,120,214,.12); color:var(--primary-color,#2a78d6);
    border-color:transparent;
  }
  section[data-testid="stSidebar"] div.stButton > button p{font-size:.92rem;}
</style>
"""


def main() -> None:
    st.logo(
        str(_STATIC / "logo.svg"), size="large",
        icon_image=str(_STATIC / "logo-icon.svg"),
    )
    polish()
    st.markdown(_NAV_CSS, unsafe_allow_html=True)
    st.session_state.setdefault("nav_view", NAV_ITEMS[0][0])
    nav_before_this_run = st.session_state["nav_view"]

    with st.sidebar:
        for name, icon, _ in NAV_ITEMS:
            # active is read BEFORE this button's own click is known, so the
            # button just clicked always paints with its OLD state on this
            # exact run -- a real, confirmed one-click highlight lag, not a
            # hover artifact (reproduced with the mouse moved off the sidebar
            # entirely). Content is correct immediately either way, since
            # `view(ws)` below reads the already-updated session_state; only
            # the sidebar's own paint of ITSELF lags. Fixed below.
            active = st.session_state["nav_view"] == name
            if st.button(
                f"{name}", icon=icon, key=f"nav_{name}",
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
