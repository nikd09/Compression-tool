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

_NEW_MATERIAL = "+ Add new material…"
_UPLOAD_PREFIX = "compression_tool_upload_"
_DASHBOARD_TEMPLATE_PATH = (
    Path(__file__).parent / "templates" / "results_dashboard.html"
)
# Same tuning as results_view.py / materials_view.py -- one template, three
# call sites, all sized for the same normal, screen-realistic viewport.
_DASHBOARD_FRAME_HEIGHT_PX = 820
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


def _config_from_form(detect_holds: bool) -> Config:
    d = Config()
    with st.expander("Advanced: segmentation and reference thresholds"):
        st.caption(
            "Every threshold is relative to the test's own peak stress, never "
            "absolute: the same knobs `--unload-frac` etc. expose on the "
            "command line. Defaults work unmodified for a Zwick Z100 export; "
            "change one only if Preview below shows a stage being lost or a "
            "cycle miscounted."
        )
        c1, c2 = st.columns(2)
        with c1:
            unload_frac = st.number_input(
                "unload_frac", value=d.unload_frac, format="%.3f",
                help="Stress below this fraction of peak counts as unloaded.")
            major_cycle_frac = st.number_input(
                "major_cycle_frac", value=d.major_cycle_frac, format="%.3f",
                help="A run peaking below this fraction of the global peak is noise, not a stage.")
            stiff_lo_frac = st.number_input("stiff_lo_frac", value=d.stiff_lo_frac, format="%.2f")
            stiff_hi_frac = st.number_input("stiff_hi_frac", value=d.stiff_hi_frac, format="%.2f")
        with c2:
            ref_stress_frac = st.number_input(
                "ref_stress_frac", value=d.ref_stress_frac, format="%.2f",
                help="Reference stress for cross-cycle comparison, as a fraction of the smallest cycle peak.")
            residual_stress_frac = st.number_input(
                "residual_stress_frac", value=d.residual_stress_frac, format="%.2f")
            hold_tol_frac = st.number_input("hold_tol_frac", value=d.hold_tol_frac, format="%.3f")
            h0_text = st.text_input(
                "h0_mm override", value="",
                placeholder="blank = read from the export's metadata sheet")
    h0_mm = None
    if h0_text.strip():
        try:
            h0_mm = float(h0_text)
        except ValueError:
            st.error(f"h0_mm override must be a number, got {h0_text!r}")
    return Config(
        unload_frac=unload_frac,
        major_cycle_frac=major_cycle_frac,
        stiff_lo_frac=stiff_lo_frac,
        stiff_hi_frac=stiff_hi_frac,
        ref_stress_frac=ref_stress_frac,
        residual_stress_frac=residual_stress_frac,
        hold_tol_frac=hold_tol_frac,
        h0_mm=h0_mm,
        detect_holds=detect_holds,
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


def _show_warning(w: dict) -> None:
    text = f"**{w['severity'].upper()}**: {w['message']}"
    if w["severity"] == "critical":
        st.error(text)
    elif w["severity"] == "caution":
        st.warning(text)
    else:
        st.info(text)


def _render_preview_cards(rows: list[dict]) -> None:
    for r in rows:
        if "error" in r:
            st.error(f"{Path(r['source_file']).name}: {r['error']}")
            continue
        with st.container(border=True):
            st.subheader(r["label"])
            c = st.columns(5)
            c[0].metric("Cycles", r["n_cycles"])
            c[1].metric("Holds", r["n_holds"])
            # Rounded, not full precision: this tile is a glance-check before
            # commit, not the record -- Config shows the exact ingested
            # numbers afterwards. Short enough that 5 columns fit without
            # Streamlit ellipsis-truncating the value.
            c[2].metric("Peak", f"{r['global_peak_mpa']:.0f} MPa" if r["global_peak_mpa"] else "-")
            c[3].metric("h0", f"{r['h0_mm']:.2f} mm" if r["h0_mm"] else "-")
            c[4].metric("Format", r["source_format"])
            for w in r["warnings"]:
                _show_warning(w)
            for n in r["notes"]:
                st.caption(n)


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
    components.html(state["html"], height=_DASHBOARD_FRAME_HEIGHT_PX, scrolling=True)


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

    st.divider()
    _step(2, "Thresholds", "Optional. Defaults work unmodified for a Zwick Z100 export.")
    cfg = _config_from_form(detect_holds)

    if not uploaded:
        # Nothing attached (including "no longer attached" -- the uploader
        # was cleared): no reason to keep a copy of files that are no
        # longer in the form around on disk.
        _cleanup_upload_dir()
        st.info("Upload one or more exports above to preview them.")
        return
    paths = _save_uploads(uploaded)

    st.divider()
    _step(3, "Preview", "Check format, cycle count and warnings before anything is written.")
    if st.button("Run preview", icon=":material/visibility:"):
        st.session_state["ingest_preview_rows"] = preview(
            paths, cfg, gauge_length_confirmed=gauge_confirmed
        )
        # A fresh preview invalidates any dashboard built from a previous
        # one -- drop it rather than show charts for files that may no
        # longer be the ones attached.
        st.session_state.pop("ingest_preview_dashboard", None)
    rows = st.session_state.get("ingest_preview_rows")
    if rows:
        _render_preview_cards(rows)

        if st.button(
            "Show full interactive dashboard", icon=":material/bar_chart:",
            help="The same charted dashboard the Results tab renders for an "
            "already-committed material, built here from these uploaded "
            "files directly. Nothing is written to the workspace and no "
            "material is registered by looking, so re-checking an old "
            "export or a throwaway trial never has to be Committed just to "
            "be seen.",
        ):
            st.session_state["ingest_preview_dashboard"] = _build_dashboard_preview(
                paths, cfg, material, gauge_confirmed
            )
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
            result = ingest(
                paths, ws, material=material.strip(), cfg=cfg,
                gauge_length_confirmed=gauge_confirmed,
                archive_originals=archive_originals,
                write_reports=write_reports,
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
