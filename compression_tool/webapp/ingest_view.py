"""Ingest: upload exports, preview what the engine sees before anything is
written, then commit. Mirrors `compression-tool preview` / `ingest` on the
CLI -- this view calls the exact same two functions, nothing is reimplemented."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from ..core import Config
from ..dashboard_data import MAX_SPECIMENS, build_dashboard_data
from ..material_registry import load_materials
from ..persistence import Workspace
from ..pipeline import ingest, preview, preview_dashboard_data
from .common import config_form, utm_press_html

_NEW_MATERIAL = "+ Add new material…"
_UPLOAD_PREFIX = "compression_tool_upload_"
_DASHBOARD_TEMPLATE_PATH = (
    Path(__file__).parent / "templates" / "results_dashboard.html"
)
# How long an orphaned upload directory (left behind by a session that was
# killed rather than closed normally -- a browser tab crash, a re-deployed
# server -- is kept around before the sweep below removes it. Generous on
# purpose: it only has to outlive the longest plausible single ingest, not
# protect anything -- these are ephemeral copies of files the user still has.
_STALE_UPLOAD_MAX_AGE_S = 24 * 3600
# Sweep at most once per this many seconds per server PROCESS (a module-level
# guard, not per-session): the sweep is a full directory listing, wasteful to
# repeat on every widget interaction, and it only ever needs to catch what a
# normal per-session cleanup missed.
_SWEEP_INTERVAL_S = 3600
_last_sweep = 0.0


def _step(n: int, title: str, sub: str = "") -> None:
    st.markdown(
        f'<div class="ct-step-head"><span class="ct-step">{n}</span><h3>{title}</h3></div>'
        + (f'<p class="ct-step-sub">{sub}</p>' if sub else ""),
        unsafe_allow_html=True,
    )


def _material_picker(ws: Workspace) -> str:
    """A picker, not a free-text box, once at least one material exists --
    "SteelMesh", "Steel Mesh" and "steel-mesh" typed on three different
    days become three materials that never compare against each other in
    Results or Compare, silently. Picking from what already exists is what
    prevents that; "+ Add new material" is the explicit, deliberate escape
    hatch for a genuinely new one. ingest() itself also normalizes a
    near-duplicate typed here to the existing entry as a second line of
    defence, so this is a nudge toward the right habit, not the only guard.
    """
    materials = load_materials(ws)
    if not materials:
        return st.text_input(
            "Material",
            placeholder="e.g. PEEK-GF30, the first material in this workspace",
            help="Every material typed here becomes a pickable option next "
            "time, so it never has to be retyped or matched exactly again.",
        )
    choice = st.selectbox(
        "Material", materials + [_NEW_MATERIAL], index=None,
        placeholder="Pick a material, or add a new one",
        help="Picking from this list, rather than retyping the name, is "
        "what keeps \"SteelMesh\" and \"Steel Mesh\" from silently becoming "
        "two materials that never compare against each other.",
    )
    if choice == _NEW_MATERIAL:
        return st.text_input(
            "New material name", placeholder="e.g. PEEK-GF30",
            label_visibility="collapsed",
        )
    return choice or ""


def _looks_like_a_filename(material: str, uploaded) -> bool:
    """True if `material` is basically one of the uploaded files' own
    names, not a material code -- the export's file name (something like
    "Mehrstufiger Druckversuch Vergleichstest 2 T050LR1") typed straight
    into the Material field, unedited, is exactly what turns into a
    Materials card nobody can read at a glance and that everyone still has
    to guess the right short name for in Compare. Length is the tell: a
    real material code is short; a file name pasted whole is not."""
    if not material or len(material) < 20:
        return False
    stems = {Path(f.name).stem.casefold() for f in uploaded}
    key = material.casefold()
    return any(key == stem or stem in key or key in stem for stem in stems)


def _sweep_stale_uploads() -> None:
    """Best-effort cleanup for upload directories a crashed session never
    got to remove itself -- a browser tab killed mid-upload, a server
    redeploy. Per-session cleanup (below) is the normal path; this only
    catches what that missed, so it errs generous on age and silent on
    error rather than risk removing a directory a live session still owns."""
    global _last_sweep
    now = time.time()
    if now - _last_sweep < _SWEEP_INTERVAL_S:
        return
    _last_sweep = now
    base = Path(tempfile.gettempdir())
    cutoff = now - _STALE_UPLOAD_MAX_AGE_S
    for d in base.glob(f"{_UPLOAD_PREFIX}*"):
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _cleanup_upload_dir() -> None:
    d = st.session_state.pop("ingest_upload_dir", None)
    st.session_state.pop("ingest_upload_key", None)
    st.session_state.pop("ingest_upload_paths", None)
    if d:
        shutil.rmtree(d, ignore_errors=True)


def _save_uploads(files) -> list[Path]:
    """Copy uploaded files to a temp dir the engine can read a real path
    from. Cached in session_state and keyed on (name, size) per file: a
    Streamlit rerun re-executes this on every widget interaction, not only
    when the upload actually changes, so without the cache every checkbox
    toggle while a file is attached would silently leave behind another
    copy on disk that nothing ever deleted. A new upload -- or Commit,
    which consumes the copy -- replaces or clears the cached directory
    rather than accumulating another one beside it."""
    key = tuple((f.name, f.size) for f in files)
    if (
        st.session_state.get("ingest_upload_key") == key
        and st.session_state.get("ingest_upload_dir")
        and Path(st.session_state["ingest_upload_dir"]).exists()
    ):
        return [Path(p) for p in st.session_state["ingest_upload_paths"]]

    _cleanup_upload_dir()
    tmp_dir = Path(tempfile.mkdtemp(prefix=_UPLOAD_PREFIX))
    paths = []
    for f in files:
        p = tmp_dir / f.name
        p.write_bytes(f.getbuffer())
        paths.append(p)
    st.session_state["ingest_upload_key"] = key
    st.session_state["ingest_upload_dir"] = str(tmp_dir)
    st.session_state["ingest_upload_paths"] = [str(p) for p in paths]
    return paths


def _render_preview_errors(rows: list[dict]) -> None:
    """A file that failed to parse outright, nothing else. Preview used to
    also surface every CAUTION/CRITICAL diagnostic (a discard threshold, a
    provisional strain basis) here -- dropped on request: the dashboard
    (built separately, see _build_dashboard_preview/_render_dashboard_preview)
    is now the one place those are read from, not duplicated in two places.
    A file that could not be analysed at all is different -- silently
    showing nothing for it would look like Preview did nothing, not that
    the file was rejected."""
    for r in rows:
        if "error" in r:
            st.error(f"{Path(r['source_file']).name}: {r['error']}")


def _build_dashboard_preview(
    paths: list[Path], cfg: Config, material: str, gauge_confirmed: bool
) -> dict:
    """The same charted dashboard Results renders, for files that are not
    (and may never be) committed. Built entirely in memory via
    pipeline.preview_dashboard_data() -- nothing here writes to the
    workspace or registers a material, so re-checking an old export, or
    looking at a throwaway trial, never has to become a permanent Materials
    entry just to be looked at.

    Returns a plain dict rather than rendering directly, so the result can
    be cached in session_state and redisplayed on every later rerun (a
    checkbox ticked elsewhere in the form, say) without rebuilding it --
    the same reason `preview()`'s rows are cached rather than recomputed
    every run.
    """
    result = preview_dashboard_data(
        paths, cfg,
        material=material.strip() if material and material.strip() else None,
        gauge_length_confirmed=gauge_confirmed,
    )
    html = None
    if result["payloads"]:
        data = build_dashboard_data(result["payloads"], result["curves"])
        html = _DASHBOARD_TEMPLATE_PATH.read_text(encoding="utf-8").replace(
            "/*__DATA__*/", json.dumps(data)
        )
    return {
        "skipped": result["skipped"],
        "truncated": result["truncated"],
        "total_specimens": result["total_specimens"],
        "html": html,
    }


_OPEN_IN_NEW_TAB_HEIGHT_PX = 56


def _open_in_new_tab_button(html: str, label: str = "Open dashboard in a new tab") -> None:
    """A button that opens `html` as a genuine new browser tab -- a real
    page, not an iframe embedded in the Ingest page itself.

    Not `st.link_button` to a `data:` URL: modern Chromium refuses that as
    a TOP-LEVEL navigation outright (confirmed live -- a plain `<a
    href="data:...", target="_blank">` click produces nothing, no new tab,
    no error) -- a spoofing-prevention restriction Chrome shipped some
    years back that specifically targets the data: scheme. blob: URLs are
    not covered by it: this builds the page as a Blob client-side, in the
    button's own click handler, and window.open()s the object URL that
    creates -- confirmed live to actually open a working new tab with the
    dashboard rendered.
    """
    # The dashboard template has its own <script> tags -- embedded verbatim
    # inside a JSON string, a literal "</script" in there closes THIS
    # function's own outer <script> tag early as far as the browser's HTML
    # parser is concerned (it does not know or care that it is sitting
    # inside a JS string), truncating the click handler and spilling the
    # rest of the JS as literal page text. Splitting the sequence keeps it
    # byte-identical once JS-parsed (\/ is just /) while no longer matching
    # a closing tag.
    payload = json.dumps(html).replace("</script", "<\\/script").replace("</SCRIPT", "<\\/SCRIPT")
    snippet = f"""
<div style="font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif">
<style>
  #ct-open-dashboard{{
    display:inline-flex; align-items:center; gap:.5rem;
    background:#2a78d6; color:#fff; border:none; border-radius:.4rem;
    padding:.5rem 1rem; font-size:.92rem; font-weight:600; cursor:pointer;
  }}
  #ct-open-dashboard:hover{{ filter:brightness(1.08); }}
  @media (prefers-color-scheme: dark){{
    #ct-open-dashboard{{ background:#3987e5; }}
  }}
</style>
<button id="ct-open-dashboard">↗ {label}</button>
</div>
<script>
  document.getElementById('ct-open-dashboard').addEventListener('click', function() {{
    const blob = new Blob([{payload}], {{type: 'text/html'}});
    const url = URL.createObjectURL(blob);
    window.open(url, '_blank');
  }});
</script>
"""
    components.html(snippet, height=_OPEN_IN_NEW_TAB_HEIGHT_PX)


def _render_dashboard_preview(state: dict) -> None:
    for name, why in state["skipped"]:
        st.warning(f"Skipped {name}: {why}")
    if not state["html"]:
        st.warning("Nothing to chart: every file failed to analyse, or had no cycles.")
        return
    if state["truncated"]:
        st.caption(
            f"Showing the {MAX_SPECIMENS} most recent of {state['total_specimens']} "
            f"specimens; the dashboard's colour palette has a hard "
            f"{MAX_SPECIMENS}-series limit."
        )
    _open_in_new_tab_button(state["html"])


def _with_utm_animation(caption: str, fn):
    """Runs `fn()` (a blocking call -- preview/ingest) with the UTM press
    animation shown for its duration. The animation is CSS-driven and keeps
    looping in the browser's own render loop once this markup has reached
    it, independent of Python being busy; the placeholder is what lets it
    disappear again the moment `fn()` returns, success or failure alike."""
    placeholder = st.empty()
    placeholder.markdown(utm_press_html(caption), unsafe_allow_html=True)
    try:
        return fn()
    finally:
        placeholder.empty()


def render(ws: Workspace) -> None:
    _sweep_stale_uploads()

    st.header("Ingest")
    st.caption(
        "Upload one or more exports of the same series, look before "
        "committing, then archive and index them."
    )

    _step(1, "Upload")
    uploaded = st.file_uploader(
        "Zwick Z100 export(s)", type=["xlsx"], accept_multiple_files=True,
        label_visibility="collapsed",
        help="One or more .xlsx exports of the same series. Nothing is written to the workspace until Commit.",
    )
    c1, c2 = st.columns([2, 1])
    with c1:
        material = _material_picker(ws)
    with c2:
        st.markdown("<div style='height:1.6rem'></div>", unsafe_allow_html=True)
        detect_holds = st.checkbox(
            "Test has a hold at peak", value=True,
            help="Uncheck for a fast-cycling test with no programmed dwell. "
            "A short cycle still spends a few samples turning around at peak "
            "stress (geometry, not a hold), and on a short enough cycle "
            "that turnaround can accidentally look long enough to be misread "
            "as a real one. Unchecking skips hold detection entirely, so "
            "every cycle reports no hold and no creep, instead of a few "
            "false ones scattered through an otherwise hold-free test.",
        )
        gauge_confirmed = st.checkbox(
            "Gauge length confirmed",
            help="Check this only once someone has verified the displacement "
            "channel's extensometer spans exactly this specimen's measured "
            "thickness h0, not just that h0 gives a plausible modulus. Left "
            "unchecked, strain and modulus stay provisional and carry a "
            "critical warning.",
        )

    if uploaded and _looks_like_a_filename(material, uploaded):
        st.warning(
            f"'{material}' looks like the export's file name, not a material "
            f"code. A short code (e.g. 'T050LR1') reads far better as a "
            f"Materials card and in Compare's legend - the file name itself "
            f"is already kept, in full, on every specimen record. This can "
            f"still be fixed after Commit, from the Materials tab."
        )

    st.divider()
    _step(2, "Thresholds", "Optional. Defaults work unmodified for a Zwick Z100 export.")
    cfg = config_form(detect_holds)

    if not uploaded:
        # Nothing attached (including "no longer attached" -- the uploader
        # was cleared): no reason to keep a copy of files that are no
        # longer in the form around on disk.
        _cleanup_upload_dir()
        st.info("Upload one or more exports above to preview them.")
        return
    paths = _save_uploads(uploaded)

    st.divider()
    _step(
        3, "Preview",
        "Check the files parse, then open the full interactive dashboard "
        "in a new tab, before anything is written.",
    )
    if st.button("Run preview", icon=":material/visibility:"):
        def _preview_and_build_dashboard() -> tuple[list[dict], dict]:
            # One click, one animation, both calls -- not a second button
            # that only appears (and still has to be clicked and waited on
            # again) once this one finishes. "Open dashboard in a new tab"
            # popping in once this is done IS the "preview finished, look
            # at the results" signal, not a separate step to notice and act on.
            rows_ = preview(paths, cfg, gauge_length_confirmed=gauge_confirmed)
            dashboard_ = _build_dashboard_preview(paths, cfg, material, gauge_confirmed)
            return rows_, dashboard_

        rows, dashboard_state = _with_utm_animation("Analysing…", _preview_and_build_dashboard)
        st.session_state["ingest_preview_rows"] = rows
        st.session_state["ingest_preview_dashboard"] = dashboard_state

    rows = st.session_state.get("ingest_preview_rows")
    if rows:
        _render_preview_errors(rows)
    dashboard_state = st.session_state.get("ingest_preview_dashboard")
    if dashboard_state:
        _render_dashboard_preview(dashboard_state)

    st.divider()
    _step(4, "Commit", "Archives the raw file and writes the record - re-running the same file is a no-op.")
    c1, c2 = st.columns(2)
    with c1:
        archive_originals = st.checkbox(
            "Archive a copy of the uploaded file", value=True,
            help="Copies the export into Raw exports/ before analysis, so a "
            "result can always be traced back to the exact bytes that "
            "produced it. Uncheck if you already keep your own copies "
            "elsewhere and do not want a second one on disk. The file's "
            "SHA-256 is still recorded either way, which is what a "
            "re-ingest of the same file is detected from.",
        )
    with c2:
        write_reports = st.checkbox(
            "Write per-run Excel/CSV/HTML", value=True,
            help="Writes a per-specimen and per-run Excel workbook, CSV and "
            "HTML report alongside the JSON record. Uncheck if you only "
            "open the combined report in reports/<material> (see Config) "
            "and find these per-run copies redundant. The JSON record "
            "and curve cache, which the combined report and every chart "
            "are rebuilt from, are always written either way.",
        )
    if st.button("Commit to workspace", type="primary", icon=":material/save:"):
        if not material or not material.strip():
            st.error("Pick or type a material name above before committing.")
        else:
            result = _with_utm_animation(
                "Committing…",
                lambda: ingest(
                    paths, ws, material=material.strip(), cfg=cfg,
                    gauge_length_confirmed=gauge_confirmed,
                    archive_originals=archive_originals,
                    write_reports=write_reports,
                ),
            )
            # The uploaded copy has done its job -- ingest() has already
            # archived (or hashed) and read every file -- so there is
            # nothing left for it to do on disk.
            _cleanup_upload_dir()
            if result.material != material.strip():
                st.info(
                    f"Matched to the existing material '{result.material}' "
                    f"instead of creating a near-duplicate of '{material.strip()}'."
                )
            st.success(f"Ingested {len(result.specimens)} specimen(s) into {result.run_dir}")
            st.code(result.summary())
            for name, why in result.skipped:
                st.warning(f"Skipped {name}: {why}")
