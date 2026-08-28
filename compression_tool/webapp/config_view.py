"""Config: what settings a run was actually ingested with. Read-only by
design -- changing a threshold belongs on the Ingest form, where it can be
tried against a Preview before anything is committed; this view exists so a
result can always be traced back to the exact numbers behind it, per run.

Organised as tabs, not one long scroll: "Run" (trace this run's numbers back
to their source), "Re-analyse" (the one write action here), "Exports"
(cross-run/cross-material output), "Activity" (the workspace-wide audit
trail) and "Administration" (index/admin, neither of which depends on a run
being selected at all). The run picker sits above the tabs, not inside one
of them, so switching tabs never re-asks which run you meant."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from .. import audit, knowledge_base, permissions
from ..material_export import export_material
from ..persistence import Workspace, read_json, slugify
from ..pipeline import ingest
from ..reports_overview import build_overview
from .common import config_form, short_tag


def render(ws: Workspace) -> None:
    st.header("Config")
    st.caption("What a run was actually ingested with, traced back per run, not the app's current form defaults.")

    manifests = sorted(ws.processed.glob("*/run.json")) if ws.processed.exists() else []

    manifest = None
    if manifests:
        # Rendered above the tabs, not inside one -- every tab that needs
        # "which run" reads this same selection, so switching tabs never
        # re-asks for it.
        labels = {m: f"{m.parent.name}" for m in manifests}
        chosen = st.selectbox("Run", manifests, format_func=lambda m: labels[m])
        manifest = read_json(chosen)

    tab_run, tab_reanalyze, tab_exports, tab_activity, tab_admin = st.tabs([
        ":material/description: Run",
        ":material/refresh: Re-analyse",
        ":material/download: Exports",
        ":material/history: Activity",
        ":material/admin_panel_settings: Administration",
    ])

    if manifest is None:
        for tab in (tab_run, tab_reanalyze, tab_exports, tab_activity):
            with tab:
                st.info("Nothing ingested into this workspace yet - use Ingest first.")
        with tab_admin:
            _render_administration(ws)
        return

    with tab_run:
        _render_run(manifest)
    with tab_reanalyze:
        _render_reanalyze(ws, manifest)
    with tab_exports:
        _render_exports(ws, manifest)
    with tab_activity:
        _render_activity(ws)
    with tab_admin:
        _render_administration(ws)


def _render_run(manifest: dict) -> None:
    with st.container(border=True, key="card_run_summary"):
        st.subheader(manifest.get("material", "-"))
        c1, c2, c3 = st.columns(3)
        c1.metric("Specimens", len(manifest.get("specimens", [])))
        c2.metric("Sources", len(manifest.get("sources", [])))
        # The full ISO-8601 timestamp ("2026-08-25T10:46:42+00:00") doesn't
        # fit a metric tile at any column width Streamlit's large metric font
        # allows -- even "date time" truncated to the minute still clips.
        # Date only in the tile; the exact time is one glance away, in the
        # column header via help text.
        created = manifest.get("created_utc", "-")
        c3.metric(
            "Ingested (UTC)", created[:10] if len(created) >= 10 else created,
            help=f"Full timestamp: {created}",
        )

    st.markdown("##### Sources")
    sources = manifest.get("sources", [])
    if sources:
        st.dataframe(
            pd.DataFrame(
                {
                    "File": [s.get("source_file", "-") for s in sources],
                    "sha256": [s.get("sha256", "-")[:12] + "…" for s in sources],
                }
            ),
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption("None recorded.")

    st.markdown("##### Specimens")
    specimens = manifest.get("specimens", [])
    if specimens:
        labels_ = [s.get("label", "-") for s in specimens]
        st.dataframe(
            pd.DataFrame(
                {
                    "": [short_tag(lbl, i + 1) for i, lbl in enumerate(labels_)],
                    "Label": labels_,
                    "Cycles": [s.get("n_cycles", "-") for s in specimens],
                    "JSON": [s.get("json", "-") for s in specimens],
                }
            ),
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption("None recorded.")

    with st.expander("Settings this run used"):
        st.json(manifest.get("config", {}), expanded=False)
    with st.expander("Full manifest"):
        st.json(manifest)

    st.caption(
        "This is what the run was actually ingested with, not the app's current "
        "form defaults. The two can differ once someone changes a threshold on "
        "the Ingest tab for a later run."
    )


def _render_exports(ws: Workspace, manifest: dict) -> None:
    material = manifest.get("material", "")
    reports_dir = ws.root / "reports"
    xlsx_path = reports_dir / f"{slugify(material)}.xlsx"
    html_path = reports_dir / f"{slugify(material)}.html"
    with st.container(border=True, key="card_material_export"):
        st.markdown("##### Combined across every run of this material")
        st.caption(
            "One workbook and one standalone dashboard (open the .html file "
            "directly in a browser, no server needed) covering every "
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

    overview_path = ws.root / "reports" / "_Overview.html"
    with st.container(border=True, key="card_overview_export"):
        st.markdown("##### Overview across every material")
        st.caption(
            "One page listing every material in this workspace, with its "
            "specimen/run counts, peak stress and thickness, and a link "
            "into each material's own report. Open the .html file directly "
            "in a browser, no server needed. Regenerated automatically on "
            "every Commit, from any material."
        )
        if overview_path.exists():
            st.code(str(overview_path), language=None)
        else:
            st.caption("Not built yet.")
        if st.button("Rebuild now", icon=":material/refresh:", key="rebuild_overview"):
            result = build_overview(ws)
            if result:
                st.success("Rebuilt from every indexed material.")
            else:
                st.warning("No indexed specimens found in this workspace.")


def _render_activity(ws: Workspace) -> None:
    entries = audit.list_entries(ws, limit=15)
    if not entries:
        st.info("No activity recorded yet.")
        return
    st.caption(
        "Who ingested what, and when: one record per Commit, across the "
        "whole workspace, not scoped to one run. The 15 most recent; every "
        "record ever written is a small JSON file under audit/, or "
        "`compression_tool audit` on the CLI."
    )
    st.dataframe(
        pd.DataFrame({
            "Time (UTC)": [e.get("timestamp_utc", "-") for e in entries],
            "User": [e.get("user", "-") for e in entries],
            "Host": [e.get("host", "-") for e in entries],
            "Material": [e.get("material", "-") for e in entries],
            "Specimens": [len(e.get("specimens", [])) for e in entries],
            "Skipped": [len(e.get("skipped", [])) for e in entries],
            "Run": [e.get("run_dir", "-") for e in entries],
        }),
        use_container_width=True, hide_index=True,
    )


def _render_administration(ws: Workspace) -> None:
    with st.container(border=True, key="card_index"):
        st.markdown("##### Index")
        st.caption(
            "The database every tab reads from, built from the JSON records "
            "under Records/. It only ever grows or updates when this app "
            "writes to it - if a record's file was deleted outside the app "
            "(Explorer, the shared drive) the index still lists it until "
            "reindexed, and a tab that then tries to open it will show an "
            "error instead of the material. Rebuilding is always safe: the "
            "JSON records are the source of truth, the index is only ever "
            "derived from them."
        )
        if st.button("Reindex from disk", icon=":material/refresh:", key="reindex_from_disk"):
            count = knowledge_base.rebuild(ws)
            st.success(f"Reindexed {count} specimen record(s) from disk.")

    _render_admin_access(ws)


def _reanalyze_sources(ws: Workspace, manifest: dict) -> tuple[list[Path], list[str]]:
    """This run's sources whose archived raw file is still on disk, and the
    source_file names of any that are not -- either archive_originals=False
    was used at ingest time (raw_input_path was never recorded) or the
    archived copy was later removed. Only the former can be fed back through
    ingest() without re-uploading; the caller decides what to do about the
    rest. Pure and disk-only, so it is tested directly without going through
    Streamlit.
    """
    found: list[Path] = []
    missing: list[str] = []
    for source in manifest.get("sources", []):
        rel = source.get("raw_input_path")
        path = ws.root / rel if rel else None
        if path is not None and path.exists():
            found.append(path)
        else:
            missing.append(source.get("source_file", "-"))
    return found, missing


def _render_reanalyze(ws: Workspace, manifest: dict) -> None:
    """Re-run this run's already-archived sources through the engine with
    different thresholds, without asking anyone to find and re-upload the
    original export. archive_originals=True below is deliberate even though
    the file is already archived: it is what keeps the new specimen records'
    raw_input_path pointing at that same archived copy (archive_raw() is
    content-addressed and idempotent, so re-"archiving" it costs nothing and
    copies nothing) rather than losing that link the way
    archive_originals=False would.

    Same sources under the same settings on the same day overwrite this run
    in place (resolve_run_dir's existing behaviour, by fingerprint); a
    changed setting gets its own new run folder, so the run being compared
    against never silently changes under it.
    """
    material = manifest.get("material", "")
    found, missing = _reanalyze_sources(ws, manifest)
    total = len(found) + len(missing)

    st.caption(
        "Re-run this run's archived source file(s) with different "
        "thresholds, without re-uploading anything. Change a value below "
        "only if this run's numbers look wrong and a different setting "
        "would fix it -- most runs never need this."
    )
    if not found:
        st.caption(
            "Not available - none of this run's source files were "
            "archived (ingested with 'Archive a copy of the uploaded "
            "file' unchecked on the Ingest tab), so there is nothing on "
            "disk to re-run this from."
        )
        return
    if missing:
        st.warning(
            f"{len(missing)} of {total} source file(s) for this run are "
            f"missing from the archive and will be skipped: "
            f"{', '.join(missing)}"
        )

    detect_holds = st.checkbox(
        "Test has a hold at peak", value=True, key="cfg_reanalyze_holds",
    )
    cfg = config_form(detect_holds)
    gauge_confirmed = st.checkbox(
        "Gauge length confirmed", key="cfg_reanalyze_gauge",
        help="Check this only once someone has verified the displacement "
        "channel spans exactly this specimen's h0. Left unchecked, strain "
        "and modulus stay provisional in the re-analysed record.",
    )
    st.caption(
        "This overwrites this run's stored numbers in place if the settings "
        "below fingerprint the same as today's, or creates a new run "
        "alongside it if they don't -- either way, nothing here touches the "
        "original uploaded export."
    )
    confirm = st.checkbox(
        "I understand this replaces this run's stored analysis",
        key="cfg_reanalyze_confirm",
    )
    if st.button(
        "Re-analyse now", icon=":material/refresh:", key="cfg_reanalyze_btn",
        disabled=not confirm,
    ):
        result = ingest(
            found, ws, material=material, cfg=cfg,
            gauge_length_confirmed=gauge_confirmed,
            archive_originals=True, write_reports=True,
        )
        st.success(
            f"Re-analysed into {result.run_dir} - "
            f"{len(result.specimens)} specimen(s)."
        )
        for name, why in result.skipped:
            st.warning(f"Skipped {name}: {why}")
        st.rerun()


def _render_admin_access(ws: Workspace) -> None:
    """Who may Rename or Delete a material from the Materials tab -- see
    permissions.py for what this does and does not actually enforce (a
    shared, hand-editable allowlist keyed on the OS username, not a login
    system)."""
    with st.container(border=True, key="card_admin_access"):
        st.markdown("##### Admin access")
        me = permissions.current_user()
        if not permissions.admins_configured(ws):
            st.caption(
                f"Nobody has restricted this yet - every visitor, including "
                f"you ({me}), can currently rename or delete a material from "
                f"the Materials tab. Claim admin access to restrict Rename "
                f"and Delete to specific people from here on."
            )
            if st.button("Claim admin access for myself", icon=":material/shield:"):
                permissions.claim_admin(ws)
                st.rerun()
            return

        admins = permissions.load_admins(ws)
        is_admin = permissions.is_admin(ws)
        if is_admin:
            st.caption(
                f"Signed in as {me} - listed as an admin. Rename and Delete "
                f"are visible on the Materials tab. Add or remove people below; "
                f"this only checks the OS username the app is running under, "
                f"the same identity Config's audit log already records."
            )
            st.dataframe(
                pd.DataFrame({"Admin": admins}), use_container_width=True, hide_index=True,
            )
            c1, c2 = st.columns([3, 1])
            with c1:
                new_name = st.text_input(
                    "Add admin (Windows/OS username)", key="cfg_add_admin",
                    label_visibility="collapsed", placeholder="e.g. n7215177",
                )
            with c2:
                if st.button("Add", key="cfg_add_admin_btn") and new_name.strip():
                    permissions.add_admin(ws, new_name.strip())
                    st.rerun()
            removable = [a for a in admins if a.casefold() != me.casefold()]
            if removable:
                to_remove = st.selectbox(
                    "Remove an admin", [""] + removable, key="cfg_remove_admin",
                )
                if to_remove and st.button("Remove", key="cfg_remove_admin_btn"):
                    permissions.remove_admin(ws, to_remove)
                    st.rerun()
        else:
            st.caption(
                f"Signed in as {me} - not on the admin list ({', '.join(admins)}). "
                f"Rename and Delete are hidden on the Materials tab. Ask one of "
                f"them to add you above, or edit admins.json directly at the "
                f"workspace root."
            )
