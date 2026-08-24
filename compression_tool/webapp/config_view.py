"""Config: what settings a run was actually ingested with. Read-only by
design -- changing a threshold belongs on the Ingest form, where it can be
tried against a Preview before anything is committed; this view exists so a
result can always be traced back to the exact numbers behind it, per run."""

from __future__ import annotations

import streamlit as st

from ..persistence import read_json
from .common import workspace_picker


def render() -> None:
    st.header("Config")
    ws = workspace_picker()

    manifests = sorted(ws.processed.glob("*/run.json")) if ws.processed.exists() else []
    if not manifests:
        st.info("Nothing ingested into this workspace yet — use Ingest first.")
        return

    labels = {m: f"{m.parent.name}" for m in manifests}
    chosen = st.selectbox("Run", manifests, format_func=lambda m: labels[m])
    manifest = read_json(chosen)

    st.subheader(manifest.get("material", "—"))
    c1, c2 = st.columns(2)
    c1.metric("Specimens", len(manifest.get("specimens", [])))
    c2.metric("Ingested", manifest.get("created_utc", "—"))

    st.markdown("**Settings this run used**")
    st.json(manifest.get("config", {}), expanded=False)

    st.markdown("**Sources**")
    for s in manifest.get("sources", []):
        st.text(f"{s.get('source_file', '—')}  ·  sha256 {s.get('sha256', '—')[:12]}…")

    st.markdown("**Specimens**")
    for s in manifest.get("specimens", []):
        st.text(f"{s.get('label')}  ·  {s.get('n_cycles')} cycles  ·  {s.get('json')}")

    with st.expander("Full manifest"):
        st.json(manifest)

    st.divider()
    st.caption(
        "This is what the run was actually ingested with, not the app's current "
        "form defaults — the two can differ once someone changes a threshold on "
        "the Ingest tab for a later run."
    )
