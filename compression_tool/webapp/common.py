"""Shared bits every view needs: which workspace to look at, a read-only
connection to its index, the shared style layer, and the specimen-label
helpers every specimen picker uses so they read the same way everywhere."""

from __future__ import annotations

import os
import re

import streamlit as st

from .. import knowledge_base
from ..persistence import Workspace, workspace_index_root
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

  /* Bordered containers (st.container(border=True, key="card_...")) -- the
     one card style used everywhere a view groups related controls, matching
     the dashboard's own .cell surfaces so the whole app reads as one system.

     This used to target [data-testid="stVerticalBlockBorderWrapper"], which
     turned out to not exist ANYWHERE in this Streamlit version's rendered
     DOM (confirmed live) -- border=True now styles the stVerticalBlock
     itself, via an auto-generated class shared identically by every bordered
     container, not a stable thing to select. `key=` is: Streamlit still
     guarantees a stable `st-key-<key>` class on that element regardless of
     its own internal markup churn. Every st.container(border=True) call
     site that wants this treatment opts in with a key starting "card_" --
     compare_view.py, config_view.py, ingest_view.py and materials_view.py
     (which layers its own richer, clickable-card styling in _CARD_CSS on
     top of this) all follow that convention now; a bordered container added
     later without one simply will not pick this up, the same way it never
     did before this fix -- see README, "Materials cards hover". */
  [class*="st-key-card_"]{
    border-radius:.7rem!important;
    transition:box-shadow .18s ease, border-color .18s ease;
  }
  [class*="st-key-card_"]:hover{
    box-shadow:0 3px 14px rgba(0,0,0,.07);
    border-color:rgba(42,120,214,.45)!important;
  }
  @media (prefers-color-scheme: dark){
    [class*="st-key-card_"]:hover{
      box-shadow:0 3px 14px rgba(0,0,0,.28);
      border-color:rgba(57,135,229,.55)!important;
    }
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
    background:color-mix(in srgb, var(--secondary-background-color, #f5eef1) 55%, transparent)!important;
  }
  @media (prefers-color-scheme: dark){
    [data-testid="stFileUploaderDropzone"]{
      background:color-mix(in srgb, var(--secondary-background-color, #241521) 55%, transparent)!important;
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


def utm_press_html(caption: str = "Analysing…") -> str:
    """A large, centered looping animation of a UTM crosshead compressing a
    specimen -- shown in a `st.empty()` placeholder around a call that
    blocks the script (`ingest()`, `preview()`), since that is the one place
    a CSS animation can run independently of Python: once this markup has
    actually reached the browser, the animation keeps looping in its own
    render loop for as long as the blocking call takes, with no further
    communication from the (busy) Python side needed to keep it moving.

    Rendered as a fixed, full-viewport overlay -- not inline content where
    it landed in the page -- centred regardless of scroll position or
    screen size: this used to sit right where the triggering button was,
    which on a long form (Ingest's Preview/Commit) could be well below the
    fold, invisible until someone scrolled down to it. `position:fixed` on
    the backdrop is what actually fixes that; centring an inline element
    with a scroll-into-view call would only have chased the symptom on one
    viewport size and broken on the next.

    The card itself is still the centrepiece of whatever moment it appears
    in -- a big, unmissable stand-in for "the machine is working" while
    nothing else on the page can update. The stroke shape doubles as a hint
    at what the tool is analysing rather than a generic spinner:
    down-hold-up, not a smooth back-and-forth -- echoing the
    load-dwell-unload cycle shape every specimen this tool ingests actually
    goes through.
    """
    return f"""
<div class="ct-utm-overlay">
<div class="ct-utm-wrap">
  <style>
  .ct-utm-overlay{{
    position:fixed; inset:0; z-index:99999;
    display:flex; align-items:center; justify-content:center;
    animation:ctUtmFadeIn .15s ease;
  }}
  .ct-utm-wrap{{
    display:flex;flex-direction:column;align-items:center;justify-content:center;
    gap:1.1rem;padding:2.8rem 2.4rem;border-radius:1.1rem;
    max-width:min(92vw,440px);
    background:#f9f9f7;
    box-shadow:0 24px 64px rgba(0,0,0,.35);
  }}
  @media (prefers-color-scheme: dark){{
    .ct-utm-wrap{{background:#1a1a19;}}
  }}
  @keyframes ctUtmFadeIn{{ from{{opacity:0}} to{{opacity:1}} }}
  .ct-utm-svg{{flex:none;width:220px;height:220px;overflow:visible;
    filter:drop-shadow(0 6px 18px rgba(0,0,0,.12));}}
  .ct-utm-cap{{font-size:1.15rem;font-weight:650;opacity:.85;letter-spacing:-.01em;}}
  .ct-utm-head{{animation:ctUtmPress 1.8s cubic-bezier(.5,0,.5,1) infinite;
    transform-box:fill-box;transform-origin:50% 0%;}}
  .ct-utm-specimen{{animation:ctUtmSquash 1.8s cubic-bezier(.5,0,.5,1) infinite;
    transform-box:fill-box;transform-origin:50% 100%;}}
  .ct-utm-glow{{animation:ctUtmGlow 1.8s cubic-bezier(.5,0,.5,1) infinite;
    transform-box:fill-box;transform-origin:50% 100%;}}
  .ct-utm-specimen, .ct-utm-glow{{fill:#2a78d6;}}
  /* var(--text-color) does not actually resolve in this Streamlit build
     (confirmed empty via DOM inspection, same gap as --primary-color and
     --secondary-background-color elsewhere in this file) -- its fallback,
     a near-black, is what always rendered, on every theme. Harmless in
     light mode; in dark mode it drew the whole machine frame as
     near-black on a near-black card, effectively invisible. Explicit
     per-theme fill on this class is the actual fix, not the var(). */
  .ct-utm-ink{{fill:#0b0b0b;}}
  @media (prefers-color-scheme: dark){{
    .ct-utm-specimen, .ct-utm-glow{{fill:#3987e5;}}
    .ct-utm-ink{{fill:#f4f2f1;}}
  }}
  @keyframes ctUtmPress{{
    0%{{transform:translateY(0)}}
    35%{{transform:translateY(30px)}}
    50%{{transform:translateY(33px)}}
    65%{{transform:translateY(30px)}}
    100%{{transform:translateY(0)}}
  }}
  @keyframes ctUtmSquash{{
    0%{{transform:scaleY(1)}}
    35%{{transform:scaleY(.78)}}
    50%{{transform:scaleY(.74)}}
    65%{{transform:scaleY(.78)}}
    100%{{transform:scaleY(1)}}
  }}
  @keyframes ctUtmGlow{{
    0%{{opacity:.18;transform:scaleX(1)}}
    35%{{opacity:.5;transform:scaleX(1.35)}}
    50%{{opacity:.6;transform:scaleX(1.42)}}
    65%{{opacity:.5;transform:scaleX(1.35)}}
    100%{{opacity:.18;transform:scaleX(1)}}
  }}
  @media (prefers-reduced-motion: reduce){{
    .ct-utm-head, .ct-utm-specimen, .ct-utm-glow{{animation:none;}}
  }}
  </style>
  <svg class="ct-utm-svg" viewBox="0 0 100 100" role="img" aria-label="Compressing specimen">
    <ellipse class="ct-utm-glow" cx="50" cy="84" rx="20" ry="4"/>
    <rect class="ct-utm-ink" x="14" y="6" width="8" height="82" rx="2" opacity=".35"/>
    <rect class="ct-utm-ink" x="78" y="6" width="8" height="82" rx="2" opacity=".35"/>
    <rect class="ct-utm-ink" x="10" y="2" width="80" height="8" rx="2" opacity=".55"/>
    <rect class="ct-utm-ink" x="30" y="80" width="40" height="8" rx="2" opacity=".55"/>
    <rect class="ct-utm-specimen" x="38" y="62" width="24" height="18" rx="2"/>
    <g class="ct-utm-head">
      <rect class="ct-utm-ink" x="26" y="24" width="48" height="10" rx="2" opacity=".78"/>
    </g>
  </svg>
  <span class="ct-utm-cap">{caption}</span>
</div>
</div>
"""


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
        help="Where Raw exports/, Records/ and reports/ live: every "
        "specimen's JSON, CSV, Excel workbook and HTML report land here on "
        "every Commit, nowhere else. The same path every time this app is "
        "opened shows the same tests; pointing it elsewhere switches "
        "workspaces, it does not copy anything between them. A relative path "
        "like the default is resolved against wherever `streamlit run` was "
        "launched from; see the resolved path below if that's unclear. "
        "Everything here stays local: no network calls, nothing uploaded. "
        "The search index is kept separately, on this machine only; see "
        "the note below the resolved path.",
    )
    # The searchable index is deliberately NOT under `root`: `root` is
    # expected to be a synced or shared folder (OneDrive, a network drive),
    # and syncing a SQLite file while it is being written is a well-known
    # way to corrupt it. The index is disposable and rebuilt from the JSON
    # records under `root` -- moving it off the synced path costs nothing.
    # Scoped per workspace (workspace_index_root, not the bare
    # default_index_root() base) so switching this field to a different
    # folder can never pick up a previous workspace's already-built index.
    ws = Workspace.at(root, index_root=workspace_index_root(root))
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
    database just by being opened.

    A schema mismatch (the index was built under an older/newer
    SCHEMA_VERSION than this code) shows a clear message and halts the
    script with `st.stop()` -- not returning None -- so it reads as its
    own error, not silently as "nothing indexed yet" (every caller's own
    `if conn is None` fallback is exactly that message, which would be
    actively misleading here: this workspace is not empty, its index just
    needs rebuilding). This is the one call site that catches it: Materials'
    and Config's own knowledge_base.connect() calls (material_export.py,
    reports_overview.py) are not routed through here and would still raise
    it raw, as an ordinary Streamlit traceback.
    """
    if not ws.db_path.exists():
        return None
    try:
        return knowledge_base.connect(ws.db_path)
    except knowledge_base.SchemaVersionMismatch as exc:
        st.error(str(exc))
        st.stop()
