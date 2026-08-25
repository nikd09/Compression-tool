"""Shared bits every view needs: which workspace to look at, a read-only
connection to its index, the shared style layer, and the specimen-label
helpers every specimen picker uses so they read the same way everywhere."""

from __future__ import annotations

import os
import re

import streamlit as st

from .. import knowledge_base
from ..persistence import Workspace, default_index_root
from ..pipeline import rebuild_index

# Resolved once, at import time, from an environment variable set on the
# HOST machine -- not hardcoded, so the same code works for whoever is
# hosting the app today and for whoever it moves to later; only the launch
# environment changes, never this file. Falls back to a plain relative
# folder for local development when the variable is unset.
DEFAULT_WORKSPACE = os.environ.get("COMPRESSION_TOOL_WORKSPACE", "./data")

# The validated categorical slots (light mode; Streamlit widgets do not
# follow the dashboard's dark-mode CSS variables, so these are the literal
# hex steps, not var() references) -- used for the small colour dots next to
# specimen/group names so a picker visually previews the chart colour a
# choice will get, before any chart is drawn.
CATEGORICAL_LIGHT = [
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
    "#e87ba4", "#008300", "#4a3aa7", "#e34948",
]

# Streamlit's own theme covers colour and radius (see .streamlit/config.toml);
# this covers what the theme cannot reach -- card surfaces, hover/focus
# treatment, heading rhythm, and the empty-state look -- so every view (not
# only Results) reads as one designed system rather than default widgets
# next to a custom dashboard.
_POLISH = """
<style>
  .block-container{padding-top:2.2rem;padding-bottom:3rem;max-width:1500px;}
  h1,h2,h3{letter-spacing:-.018em;}
  h1{font-size:1.75rem!important;font-weight:670!important;}
  h2{font-size:1.2rem!important;font-weight:650!important;}
  h3{font-size:1.02rem!important;font-weight:640!important;}
  [data-testid="stSidebarHeader"]{padding-bottom:.4rem;}
  section[data-testid="stSidebar"] h1{font-size:1.05rem!important;}
  /* The dashboard iframe brings its own card surfaces; a second border around
     it would read as a frame inside a frame. */
  iframe[title="st.iframe"]{border:none!important;}
  div[data-testid="stMetricValue"]{font-variant-numeric:tabular-nums;}
  div[data-testid="stMetricLabel"]{font-size:.72rem;text-transform:uppercase;
    letter-spacing:.06em;font-weight:650;opacity:.72;}

  /* Bordered containers (st.container(border=True)) -- the one card style
     used everywhere a view groups related controls, matching the dashboard
     .cell surfaces so the whole app reads as one system. */
  div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div[data-testid="stVerticalBlock"]){
    border-radius:.7rem!important;
    transition:box-shadow .15s ease, border-color .15s ease;
  }
  div[data-testid="stVerticalBlockBorderWrapper"]:hover{
    box-shadow:0 2px 10px rgba(0,0,0,.05);
  }

  /* File uploader: a calmer dashed drop-zone instead of the default block.
     var(--secondary-background-color) was confirmed (DOM inspection) to
     resolve to nothing in this Streamlit build -- it is not defined as a
     custom property anywhere in the page, despite being documented. The
     fallback is this app's own [theme] secondaryBackgroundColor literal, so
     the rule still does something instead of silently no-op'ing. The dark
     variant below matches [theme.dark], since the app does follow the
     browser's prefers-color-scheme regardless of [theme] base. */
  [data-testid="stFileUploaderDropzone"]{
    border-radius:.7rem!important;
    background:color-mix(in srgb, var(--secondary-background-color, #f2f1ed) 55%, transparent)!important;
  }
  @media (prefers-color-scheme: dark){
    [data-testid="stFileUploaderDropzone"]{
      background:color-mix(in srgb, var(--secondary-background-color, #232322) 55%, transparent)!important;
    }
  }

  /* Buttons: consistent weight/radius everywhere, primary gets a touch of lift. */
  .stButton>button, .stDownloadButton>button{
    border-radius:.55rem!important;font-weight:600!important;
  }
  .stButton>button[kind="primary"]{box-shadow:0 1px 3px rgba(0,0,0,.14);}

  /* Expanders: a slightly firmer header so they read as controls, not text. */
  [data-testid="stExpander"] summary{font-weight:610!important;}

  /* Numbered step badge used by the Ingest flow. var(--primary-color) has
     the same no-such-custom-property gap as --secondary-background-color
     above (confirmed empty via DOM inspection) -- same fix, same reasoning. */
  .ct-step{display:inline-flex;align-items:center;justify-content:center;
    width:1.55rem;height:1.55rem;border-radius:50%;
    background:var(--primary-color, #2a78d6);color:#fff;font-weight:700;font-size:.82rem;
    margin-right:.55rem;flex:none;}
  @media (prefers-color-scheme: dark){
    .ct-step{background:var(--primary-color, #3987e5);}
  }
  .ct-step-head{display:flex;align-items:center;gap:.1rem;margin-bottom:.15rem;}
  .ct-step-head h3{margin:0!important;}
  .ct-step-sub{margin:0 0 .9rem 2.15rem;font-size:.85rem;opacity:.72;}

  /* Small colour swatch, inline with text -- specimen/group colour preview. */
  .ct-dot{display:inline-block;width:9px;height:9px;border-radius:2.5px;
    margin-right:.4rem;vertical-align:1px;}
</style>
"""


def polish() -> None:
    """Apply the shared style layer. Called once, from app.py."""
    st.markdown(_POLISH, unsafe_allow_html=True)


def dot(i: int) -> str:
    """An inline HTML colour swatch for categorical slot i, e.g. for a
    specimen or comparison-group label. Cycles past 8 rather than raising --
    a label swatch overshooting the palette is a cosmetic rollover, not the
    hard stop MAX_SPECIMENS enforces where the swatch is load-bearing."""
    colour = CATEGORICAL_LIGHT[i % len(CATEGORICAL_LIGHT)]
    return f'<span class="ct-dot" style="background:{colour}"></span>'


_SAMPLE_SUFFIX = re.compile(r"[_\s-](S\d+)$", re.IGNORECASE)


def short_tag(label: str, fallback_index: int) -> str:
    """A short, stable tag for one specimen label -- 'S1', 'S2', ... pulled
    from the label's own trailing _S<n> (the convention every ingested label
    already carries), or '#N' if a label does not follow it.

    Exists because a picker showing full labels side by side breaks when
    labels share a long common prefix (the same source filename): a
    truncated dropdown or chip cuts off exactly the suffix that told two
    specimens apart, leaving entries that read as identical. Put first, this
    survives that truncation.
    """
    m = _SAMPLE_SUFFIX.search(label)
    return m.group(1).upper() if m else f"#{fallback_index}"


def workspace_picker() -> Workspace:
    """Render the ONE Workspace input for the whole app session.

    Called exactly once, from app.py, never from inside a view. A
    `key="workspace_root"` widget re-declared at a second call site (e.g. a
    second `st.text_input(key="workspace_root")` inside a different view
    function) is, to Streamlit, a *different* widget instance sharing that
    key -- and switching to it drops the session_state value back to the
    widget's own default instead of carrying it over. Confirmed with an
    isolated repro: the same key at two call sites resets on every switch; a
    single call site does not. Every view receives the resolved `Workspace`
    as a plain argument instead of calling this again.
    """
    st.session_state.setdefault("workspace_root", DEFAULT_WORKSPACE)
    # Plain st.text_input, not st.sidebar.text_input: this is always called
    # from inside app.py's own `with st.sidebar:` block now, and mixing the
    # explicit st.sidebar.* API with an ambient `with st.sidebar:` context
    # was the remaining cause of the reset -- each re-enters the sidebar
    # container by a slightly different path, which is exactly the kind of
    # position-sensitivity a keyed widget's carried-over value turned out to
    # be sensitive to.
    root = st.text_input(
        "Workspace",
        key="workspace_root",
        help="Where Raw exports/, Records/ and reports/ live -- every "
        "specimen's JSON, CSV, Excel workbook and HTML report land here on "
        "every Commit, nowhere else. The same path every time this app is "
        "opened shows the same tests; pointing it elsewhere switches "
        "workspaces, it does not copy anything between them. A relative path "
        "like the default is resolved against wherever `streamlit run` was "
        "launched from -- see the resolved path below if that's unclear. "
        "Everything here stays local: no network calls, nothing uploaded. "
        "The search index is kept separately, on this machine only -- see "
        "the note below the resolved path.",
    )
    # The searchable index is deliberately NOT under `root`: `root` is
    # expected to be a synced or shared folder (OneDrive, a network drive),
    # and syncing a SQLite file while it is being written is a well-known
    # way to corrupt it. The index is disposable and rebuilt from the JSON
    # records under `root` -- moving it off the synced path costs nothing.
    ws = Workspace.at(root, index_root=default_index_root())
    if not ws.db_path.exists() and ws.processed.exists() and any(ws.processed.iterdir()):
        # First time this workspace has been opened from THIS machine (or
        # the local index cache was cleared): there is real data under
        # `root` but nothing indexed locally yet. Build it once now rather
        # than showing an empty app in front of a non-empty workspace.
        rebuild_index(ws)
    # A relative path (the default, "./data") silently depends on the launch
    # directory -- shown resolved so it is never ambiguous where a run
    # actually landed on disk.
    st.caption(f"→ `{ws.root.resolve()}`")
    st.caption(f"Index (this machine only) → `{ws.db_path.parent}`")
    return ws


def connect_readonly(ws: Workspace):
    """A connection for browsing, or None if this workspace has nothing
    indexed yet. Read-only views say so plainly rather than create an empty
    database just by being opened."""
    if not ws.db_path.exists():
        return None
    return knowledge_base.connect(ws.db_path)
