"""Ingest: upload exports, preview what the engine sees before anything is
written, then commit. Mirrors `compression-tool preview` / `ingest` on the
CLI -- this view calls the exact same two functions, nothing is reimplemented."""

from __future__ import annotations

import tempfile
from pathlib import Path

import streamlit as st

from ..core import Config
from ..pipeline import ingest, preview
from .common import polish, workspace_picker


def _config_from_form() -> Config:
    d = Config()
    with st.expander("Advanced: segmentation and reference thresholds"):
        st.caption(
            "Every threshold is relative to the test's own peak stress, never "
            "absolute -- the same knobs `--unload-frac` etc. expose on the "
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
    )


def _save_uploads(files) -> list[Path]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="compression_tool_upload_"))
    paths = []
    for f in files:
        p = tmp_dir / f.name
        p.write_bytes(f.getbuffer())
        paths.append(p)
    return paths


def _show_warning(w: dict) -> None:
    text = f"**{w['severity'].upper()}** — {w['message']}"
    if w["severity"] == "critical":
        st.error(text)
    elif w["severity"] == "caution":
        st.warning(text)
    else:
        st.info(text)


def render() -> None:
    polish()
    st.header("Ingest")
    ws = workspace_picker()

    uploaded = st.file_uploader(
        "Zwick Z100 export(s)", type=["xlsx"], accept_multiple_files=True,
        help="One or more .xlsx exports of the same series. Nothing is written to the workspace until Commit.",
    )
    material = st.text_input(
        "Material", placeholder="e.g. PEEK-GF30 -- blank infers it from the filename")
    gauge_confirmed = st.checkbox(
        "Gauge length confirmed",
        help="Check this only once someone has verified the displacement "
        "channel's extensometer spans exactly this specimen's measured "
        "thickness h0 -- not just that h0 gives a plausible modulus. Left "
        "unchecked, strain and modulus stay provisional and carry a "
        "critical warning.",
    )
    cfg = _config_from_form()

    if not uploaded:
        st.info("Upload one or more exports to preview them.")
        return

    paths = _save_uploads(uploaded)

    if st.button("Preview"):
        for r in preview(paths, cfg, gauge_length_confirmed=gauge_confirmed):
            if "error" in r:
                st.error(f"{Path(r['source_file']).name}: {r['error']}")
                continue
            with st.container(border=True):
                st.subheader(r["label"])
                c = st.columns(5)
                c[0].metric("Cycles", r["n_cycles"])
                c[1].metric("Holds", r["n_holds"])
                c[2].metric("Peak", f"{r['global_peak_mpa']:.1f} MPa" if r["global_peak_mpa"] else "—")
                c[3].metric("h0", f"{r['h0_mm']} mm" if r["h0_mm"] else "—")
                c[4].metric("Format", r["source_format"])
                for w in r["warnings"]:
                    _show_warning(w)
                for n in r["notes"]:
                    st.caption(n)

    st.divider()
    if st.button("Commit to workspace", type="primary"):
        result = ingest(
            paths, ws, material=material or None, cfg=cfg,
            gauge_length_confirmed=gauge_confirmed,
        )
        st.success(f"Ingested {len(result.specimens)} specimen(s) into {result.run_dir}")
        st.code(result.summary())
        for name, why in result.skipped:
            st.warning(f"Skipped {name}: {why}")
