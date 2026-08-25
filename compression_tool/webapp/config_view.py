"""Config: what settings a run was actually ingested with. Read-only by
design -- changing a threshold belongs on the Ingest form, where it can be
tried against a Preview before anything is committed; this view exists so a
result can always be traced back to the exact numbers behind it, per run."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ..material_export import export_material
from ..persistence import Workspace, read_json, slugify
from .common import short_tag


def render(ws: Workspace) -> None:
    st.header("Config")
    st.caption("What a run was actually ingested with -- traced back per run, not the app's current form defaults.")

    manifests = sorted(ws.processed.glob("*/run.json")) if ws.processed.exists() else []
    if not manifests:
        st.info("Nothing ingested into this workspace yet — use Ingest first.")
        return

    labels = {m: f"{m.parent.name}" for m in manifests}
    chosen = st.selectbox("Run", manifests, format_func=lambda m: labels[m])
    manifest = read_json(chosen)

    with st.container(border=True):
        st.subheader(manifest.get("material", "—"))
        c1, c2, c3 = st.columns(3)
        c1.metric("Specimens", len(manifest.get("specimens", [])))
        c2.metric("Sources", len(manifest.get("sources", [])))
        # The full ISO-8601 timestamp ("2026-08-25T10:46:42+00:00") doesn't
        # fit a metric tile at any column width Streamlit's large metric font
        # allows -- even "date time" truncated to the minute still clips.
        # Date only in the tile; the exact time is one glance away, in the
        # column header via help text.
        created = manifest.get("created_utc", "—")
        c3.metric(
            "Ingested (UTC)", created[:10] if len(created) >= 10 else created,
            help=f"Full timestamp: {created}",
        )

    material = manifest.get("material", "")
    material_dir = ws.root / "materials"
    xlsx_path = material_dir / f"{slugify(material)}.xlsx"
    html_path = material_dir / f"{slugify(material)}.html"
    with st.container(border=True):
        st.markdown("##### Combined across every run of this material")
        st.caption(
            "One workbook and one standalone dashboard (open the .html file "
            "directly in a browser -- no server needed) covering every "
            "specimen ever ingested for this material, not just this run. "
            "Regenerated automatically on every Commit; rebuild manually "
            "below if this predates that or looks stale."
        )
        if xlsx_path.exists():
            st.code(f"{xlsx_path}\n{html_path}", language=None)
        else:
            st.caption("Not built yet for this material.")
        if st.button("Rebuild now", icon=":material/refresh:", key="rebuild_material_export"):
            result = export_material(ws, material)
            if result["xlsx"]:
                st.success(f"Rebuilt from every indexed specimen of {material!r}.")
            else:
                st.warning(f"No indexed specimens found for {material!r}.")

    with st.expander("Settings this run used"):
        st.json(manifest.get("config", {}), expanded=False)

    st.markdown("##### Sources")
    sources = manifest.get("sources", [])
    if sources:
        st.dataframe(
            pd.DataFrame(
                {
                    "File": [s.get("source_file", "—") for s in sources],
                    "sha256": [s.get("sha256", "—")[:12] + "…" for s in sources],
                }
            ),
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption("None recorded.")

    st.markdown("##### Specimens")
    specimens = manifest.get("specimens", [])
    if specimens:
        labels_ = [s.get("label", "—") for s in specimens]
        st.dataframe(
            pd.DataFrame(
                {
                    "": [short_tag(lbl, i + 1) for i, lbl in enumerate(labels_)],
                    "Label": labels_,
                    "Cycles": [s.get("n_cycles", "—") for s in specimens],
                    "JSON": [s.get("json", "—") for s in specimens],
                }
            ),
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption("None recorded.")

    with st.expander("Full manifest"):
        st.json(manifest)

    st.divider()
    st.caption(
        "This is what the run was actually ingested with, not the app's current "
        "form defaults — the two can differ once someone changes a threshold on "
        "the Ingest tab for a later run."
    )
