"""Shared bits every view needs: which workspace to look at, and a read-only
connection to its index."""

from __future__ import annotations


import streamlit as st

from .. import knowledge_base
from ..persistence import Workspace

DEFAULT_WORKSPACE = "./data"

# Streamlit's own theme covers colour and radius (see .streamlit/config.toml);
# this covers only what the theme cannot reach -- heading rhythm, the width of
# the content column, and the empty-state look. Kept deliberately small: every
# rule here is one the theme has no key for.
_POLISH = """
<style>
  .block-container{padding-top:2.2rem;padding-bottom:3rem;max-width:1500px;}
  h1,h2,h3{letter-spacing:-.018em;}
  h1{font-size:1.75rem!important;font-weight:670!important;}
  h2{font-size:1.2rem!important;font-weight:650!important;}
  [data-testid="stSidebarHeader"]{padding-bottom:.4rem;}
  section[data-testid="stSidebar"] h1{font-size:1.05rem!important;}
  /* The dashboard iframe brings its own card surfaces; a second border around
     it would read as a frame inside a frame. */
  iframe[title="st.iframe"]{border:none!important;}
  div[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;}
</style>
"""


def polish() -> None:
    """Apply the shared style layer. Called once per view, at the top."""
    st.markdown(_POLISH, unsafe_allow_html=True)


def workspace_picker() -> Workspace:
    st.session_state.setdefault("workspace_root", DEFAULT_WORKSPACE)
    root = st.sidebar.text_input(
        "Workspace",
        key="workspace_root",
        help="Where raw_input/, processed_output/ and the index live. The "
        "same path every time this app is opened shows the same tests; "
        "pointing it elsewhere switches workspaces, it does not copy "
        "anything between them.",
    )
    return Workspace.at(root)


def connect_readonly(ws: Workspace):
    """A connection for browsing, or None if this workspace has nothing
    indexed yet. Read-only views say so plainly rather than create an empty
    database just by being opened."""
    if not ws.db_path.exists():
        return None
    return knowledge_base.connect(ws.db_path)
