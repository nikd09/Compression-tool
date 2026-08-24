"""Shared bits every view needs: which workspace to look at, and a read-only
connection to its index."""

from __future__ import annotations

from typing import Optional

import streamlit as st

from .. import knowledge_base
from ..persistence import Workspace

DEFAULT_WORKSPACE = "./data"


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
