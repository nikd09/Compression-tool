"""Config: what settings a run was actually ingested with. Read-only by
design -- changing a threshold belongs on the Ingest form, where it can be
tried against a Preview before anything is committed; this view exists so a
result can always be traced back to the exact numbers behind it, per run.

Organised as tabs, not one long scroll: "Run" (trace this run's numbers back
to their source), "Re-analyse" (the one write action here), "Exports"
(cross-run/cross-material output), "Activity" (the workspace-wide audit
trail) and "Maintenance" (index rebuild, which does not depend on a run
being selected at all). The run picker sits above the tabs, not inside one
of them, so switching tabs never re-asks which run you meant."""

from __future__ import annotations

import dataclasses
import re
from datetime import datetime
from pathlib import Path, PureWindowsPath

import pandas as pd
import streamlit as st

from .. import audit, knowledge_base
from ..material_admin import delete_run
from ..core import Config
from ..material_export import export_material
from ..persistence import Workspace, read_json, slugify
from ..pipeline import ingest
from ..reports_overview import build_overview
from .common import _CONFIG_FORM_T as _FORM_T
from .common import config_form, dashboard_lang, short_tag, with_utm_animation

# EN/DE strings for this page's own chrome. Threaded through every helper as
# an `L` closure, same pattern as overview_view.py/materials_view.py/
# compare_view.py -- one shared sidebar toggle (app.py), not a separate one
# per page (see common.language_picker's own docstring for why).
_T = {
    "header": {"en": "Config", "de": "Konfiguration"},
    "subtitle": {"en": 'The settings and files behind each saved run, and tools to correct or tidy up the library.', "de": 'Einstellungen und Dateien hinter jedem gespeicherten Lauf, und Werkzeuge, um die Bibliothek zu korrigieren oder aufzuräumen.'},
    "run": {"en": "Run", "de": "Lauf"},
    "tab_run": {"en": "Run", "de": "Lauf"},
    "tab_reanalyze": {"en": "Re-analyse", "de": "Neu analysieren"},
    "tab_exports": {"en": "Exports", "de": "Exporte"},
    "tab_activity": {"en": "Activity", "de": "Aktivität"},
    "tab_admin": {"en": "Maintenance", "de": "Wartung"},
    "no_data": {"en": 'Nothing saved in this library yet - use Ingest first.', "de": 'In dieser Bibliothek ist noch nichts gespeichert - zuerst unter „Einlesen“ eine Datei hinzufügen.'},
    # -- Run tab --
    "specimens": {"en": "Specimens", "de": "Proben"},
    "sources": {"en": "Sources", "de": "Quellen"},
    "ingested_utc": {"en": 'Saved', "de": 'Gespeichert'},
    "full_timestamp": {"en": "Full timestamp: {ts}", "de": "Vollständiger Zeitstempel: {ts}"},
    "sources_heading": {"en": "Sources", "de": "Quellen"},
    "none_recorded": {"en": "None recorded.", "de": "Keine erfasst."},
    "specimens_heading": {"en": "Specimens", "de": "Proben"},
    "col_file": {"en": "File", "de": "Datei"},
    "col_label": {"en": "Label", "de": "Bezeichnung"},
    "col_cycles": {"en": "Cycles", "de": "Zyklen"},
    "col_json": {"en": 'Record file', "de": 'Datensatzdatei'},
    "settings_used": {"en": "Settings this run used", "de": "Von diesem Lauf verwendete Einstellungen"},
    "col_setting": {"en": "Setting", "de": "Einstellung"},
    "col_value": {"en": "Value", "de": "Wert"},
    "run_footer": {"en": 'These are the settings this run was saved with. A later run can use different ones.', "de": 'Mit diesen Einstellungen wurde dieser Lauf gespeichert. Ein späterer Lauf kann andere verwenden.'},
    # -- Exports tab --
    "combined_heading": {"en": "Combined across every run of this material",
        "de": "Zusammengeführt über alle Läufe dieses Materials"},
    "combined_caption": {"en": 'An English and a German workbook plus one dashboard file (opens in any browser) with every specimen of this material. They are updated on every save; rebuild them here if they look out of date.', "de": 'Eine englische und eine deutsche Arbeitsmappe sowie eine Dashboard-Datei (öffnet in jedem Browser) mit allen Proben dieses Materials. Sie werden bei jedem Speichern aktualisiert; hier neu erstellen, falls sie veraltet wirken.'},
    "not_built_material": {"en": "Not built yet for this material.", "de": "Für dieses Material noch nicht erstellt."},
    "rebuild_now": {"en": "Rebuild now", "de": "Jetzt neu erstellen"},
    "rebuilding": {"en": "Rebuilding...", "de": "Wird neu erstellt…"},
    "rebuilt_material": {"en": 'Rebuilt from every specimen of {material!r}.', "de": 'Neu erstellt aus allen Proben von {material!r}.'},
    "no_indexed_material": {"en": 'No specimens found for {material!r}.', "de": 'Keine Proben für {material!r} gefunden.'},
    "overview_heading": {"en": "Overview across every material", "de": "Übersicht über alle Materialien"},
    "overview_caption": {"en": "One page listing every material in this library with a link to each material's dashboard. Opens in any browser and is updated on every save.", "de": 'Eine Seite mit allen Materialien dieser Bibliothek und einem Link zum Dashboard jedes Materials. Öffnet in jedem Browser und wird bei jedem Speichern aktualisiert.'},
    "not_built": {"en": "Not built yet.", "de": "Noch nicht erstellt."},
    "rebuilt_overview": {"en": 'Rebuilt from every material.', "de": 'Aus allen Materialien neu erstellt.'},
    "no_indexed_workspace": {"en": 'No specimens found in this library.', "de": 'Keine Proben in dieser Bibliothek gefunden.'},
    # -- Activity tab --
    "no_activity": {"en": "No activity recorded yet.", "de": "Noch keine Aktivität erfasst."},
    "activity_caption": {"en": 'Who saved what, and when. The 15 most recent saves in this library.', "de": 'Wer was wann gespeichert hat. Die 15 letzten Speichervorgänge in dieser Bibliothek.'},
    "col_time": {"en": 'Time', "de": 'Zeit'},
    "col_user": {"en": "User", "de": "Benutzer"},
    "col_host": {"en": 'Computer', "de": 'Computer'},
    "col_material": {"en": "Material", "de": "Material"},
    "col_specimens": {"en": "Specimens", "de": "Proben"},
    "col_skipped": {"en": "Skipped", "de": "Übersprungen"},
    "col_run": {"en": "Run", "de": "Lauf"},
    # -- Administration tab --
    "index_heading": {"en": 'Sample list', "de": 'Probenliste'},
    "index_caption": {"en": 'Rebuilds the list of samples from the files in the Records folder. Use it when Results says a record no longer exists, for example after files were deleted by hand. Always safe.', "de": 'Baut die Probenliste aus den Dateien im Ordner Records neu auf. Verwenden, wenn Ergebnisse meldet, dass ein Datensatz nicht mehr existiert, zum Beispiel nachdem Dateien von Hand gelöscht wurden. Immer gefahrlos.'},
    "reanalyze_all_heading": {"en": "Re-analyse all runs", "de": "Alle Läufe neu analysieren"},
    "reanalyze_all_caption": {"en": 'Recalculates every saved run from its archived original file, each with its own settings. Use it once after a tool update that changes the calculation, so all results follow the same method.', "de": 'Berechnet jeden gespeicherten Lauf aus seiner archivierten Originaldatei neu, jeweils mit seinen eigenen Einstellungen. Einmal nach einem Tool-Update verwenden, das die Berechnung ändert, damit alle Ergebnisse derselben Methode folgen.'},
    "reanalyze_all_confirm": {"en": "I understand this replaces the stored analysis of every run",
        "de": "Mir ist klar, dass dies die gespeicherte Analyse aller Läufe ersetzt"},
    "reanalyze_all_button": {"en": "Re-analyse all runs", "de": "Alle Läufe neu analysieren"},
    "reanalyze_all_done": {"en": "Re-analysed {n} run(s).", "de": "{n} Lauf/Läufe neu analysiert."},
    "reanalyze_all_skipped": {"en": "Skipped (original file not archived): {names}",
        "de": "Übersprungen (Originaldatei nicht archiviert): {names}"},
    "reanalyze_all_failed": {"en": "Failed: {name}: {why}", "de": "Fehlgeschlagen: {name}: {why}"},
    "delete_run_heading": {"en": "Delete this run", "de": "Diesen Lauf löschen"},
    "delete_run_caption": {
        "en": "Removes this one saved import and its specimens, for example a wrong or "
              "duplicate upload. The rest of the material stays. The archived original "
              "file is kept. Type the run name to confirm.",
        "de": "Entfernt diesen einen gespeicherten Import und seine Proben, zum Beispiel "
              "einen falschen oder doppelten Upload. Der Rest des Materials bleibt. Die "
              "archivierte Originaldatei bleibt erhalten. Zur Bestätigung den Laufnamen "
              "eintippen."},
    "delete_run_confirm": {"en": "Run name", "de": "Laufname"},
    "delete_run_button": {"en": "Delete run", "de": "Lauf löschen"},
    "delete_run_done": {"en": "Deleted {run} ({n} specimen(s)).",
        "de": "{run} gelöscht ({n} Probe(n))."},
    "delete_run_material_gone": {"en": "That was the last run of {material}, so the material was removed too.",
        "de": "Das war der letzte Lauf von {material}, daher wurde auch das Material entfernt."},
    "delete_run_locked": {
        "en": "{n} file(s) in {run} could not be deleted (Windows or OneDrive still has "
              "them open). Close Explorer windows on that folder, wait for OneDrive to "
              "finish syncing, then delete the run again.",
        "de": "{n} Datei(en) in {run} konnten nicht gelöscht werden (Windows oder OneDrive "
              "hält sie noch geöffnet). Explorer-Fenster dieses Ordners schließen, warten, "
              "bis OneDrive fertig synchronisiert hat, dann den Lauf erneut löschen."},
    "delete_run_empty_folder": {
        "en": "The empty folder {run} could not be removed (OneDrive is still syncing it). "
              "It is harmless and can be deleted by hand later.",
        "de": "Der leere Ordner {run} konnte nicht entfernt werden (OneDrive synchronisiert "
              "ihn noch). Er ist harmlos und kann später von Hand gelöscht werden."},
    "reindex": {"en": "Rebuild sample list", "de": "Probenliste neu aufbauen"},
    "reindexing": {"en": 'Rebuilding the list...', "de": 'Liste wird neu aufgebaut…'},
    "reindexed": {"en": 'Rebuilt the list: {n} sample(s).', "de": 'Liste neu aufgebaut: {n} Probe(n).'},
    # -- Re-analyse tab --
    "reanalyze_caption": {"en": 'Recalculate this run from its archived original with different thresholds, without uploading it again. Most runs never need this.', "de": 'Diesen Lauf aus seiner archivierten Originaldatei mit anderen Schwellenwerten neu berechnen, ohne ihn erneut hochzuladen. Die meisten Läufe brauchen das nie.'},
    "reanalyze_unavailable": {"en": 'Not available: the original file of this run was not archived when it was saved.', "de": 'Nicht verfügbar: die Originaldatei dieses Laufs wurde beim Speichern nicht archiviert.'},
    "reanalyze_missing_warning": {
        "en": "{n} of {total} source file(s) for this run are missing from the "
              "archive and will be skipped: {names}",
        "de": "{n} von {total} Quelldatei(en) dieses Laufs fehlen im Archiv und "
              "werden übersprungen: {names}"},
    "reanalyze_overwrite_note": {"en": 'The run is replaced by the new result; it is not saved a second time. The original export is never changed.', "de": 'Der Lauf wird durch das neue Ergebnis ersetzt, nicht ein zweites Mal gespeichert. Der ursprüngliche Export wird nie verändert.'},
    "reanalyze_confirm": {"en": "I understand this replaces this run's stored analysis",
        "de": "Mir ist klar, dass dies die gespeicherte Analyse dieses Laufs ersetzt"},
    "reanalyze_now": {"en": "Re-analyse now", "de": "Jetzt neu analysieren"},
    "reanalyzing": {"en": "Re-analysing...", "de": "Wird neu analysiert…"},
    "reanalyzed_success": {"en": 'Re-analysed {run_dir} - {n} specimen(s).', "de": '{run_dir} neu analysiert - {n} Probe(n).'},
    "skipped_warning": {"en": "Skipped {name}: {why}", "de": "{name} übersprungen: {why}"},
}


def render(ws: Workspace) -> None:
    lang = dashboard_lang()

    def L(key: str, **kw) -> str:
        s = _T[key][lang]
        return s.format(**kw) if kw else s

    st.header(L("header"))
    st.caption(L("subtitle"))
    for msg in st.session_state.pop("cfg_flash", []):
        st.success(msg)

    manifests = sorted(ws.processed.glob("*/run.json")) if ws.processed.exists() else []

    manifest = None
    if manifests:
        # Rendered above the tabs, not inside one -- every tab that needs
        # "which run" reads this same selection, so switching tabs never
        # re-asks for it.
        labels = {m: f"{m.parent.name}" for m in manifests}
        chosen = st.selectbox(L("run"), manifests, format_func=lambda m: labels[m], key="cfg_run")
        manifest = read_json(chosen)

    tab_run, tab_reanalyze, tab_exports, tab_activity, tab_admin = st.tabs([
        f":material/description: {L('tab_run')}",
        f":material/refresh: {L('tab_reanalyze')}",
        f":material/download: {L('tab_exports')}",
        f":material/history: {L('tab_activity')}",
        f":material/admin_panel_settings: {L('tab_admin')}",
    ])

    if manifest is None:
        for tab in (tab_run, tab_reanalyze, tab_exports, tab_activity):
            with tab:
                st.info(L("no_data"))
        with tab_admin:
            _render_administration(ws, L)
        return

    with tab_run:
        _render_run(manifest, L)
        _render_delete_run(ws, chosen.parent, L)
    with tab_reanalyze:
        _render_reanalyze(ws, manifest, L, chosen.parent)
    with tab_exports:
        _render_exports(ws, manifest, L)
    with tab_activity:
        _render_activity(ws, L)
    with tab_admin:
        _render_administration(ws, L)


def _render_delete_run(ws: Workspace, run_dir: Path, L) -> None:
    with st.container(border=True, key="card_delete_run"):
        st.markdown(f"##### {L('delete_run_heading')}")
        st.caption(L("delete_run_caption"))
        typed = st.text_input(
            L("delete_run_confirm"), key=f"cfg_delete_run_{run_dir.name}",
            placeholder=run_dir.name,
        )
        if st.button(
            L("delete_run_button"), icon=":material/delete:", key="cfg_delete_run_btn",
            disabled=typed.strip() != run_dir.name,
        ):
            out = with_utm_animation(L("reindexing"), lambda: delete_run(ws, run_dir))
            # The run picker above still points at the folder that is now
            # gone, so start the page over and say what happened at the top.
            st.session_state.pop("cfg_run", None)
            flash = [L("delete_run_done", run=run_dir.name, n=out["removed_specimens"])]
            if out["material_removed"]:
                flash.append(L("delete_run_material_gone", material=out["material"]))
            if out["files_left"]:
                flash.append(L("delete_run_locked", n=out["files_left"], run=run_dir.name))
            elif not out["folder_removed"]:
                flash.append(L("delete_run_empty_folder", run=run_dir.name))
            st.session_state["cfg_flash"] = flash
            st.rerun()


# The settings a user can change on the Thresholds form, by the label they
# carry there (not the internal field names).
_SETTING_LABELS = (
    ("unload_frac", "unload_sensitivity"),
    ("major_cycle_frac", "min_cycle_size"),
    ("stiff_lo_frac", "stiff_lo"),
    ("stiff_hi_frac", "stiff_hi"),
    ("residual_stress_frac", "reference_stress"),
    ("hold_tol_frac", "hold_tol"),
    ("h0_mm", "h0_override"),
)


def _settings_table(cfg: dict, L) -> pd.DataFrame:
    lang = dashboard_lang()
    rows = []
    for key, label_key in _SETTING_LABELS:
        value = cfg.get(key)
        if key == "residual_stress_frac" and cfg.get("residual_stress_mpa"):
            value = f"{cfg['residual_stress_mpa']:g} MPa"
        rows.append({L("col_setting"): _FORM_T[label_key][lang],
                     L("col_value"): "-" if value is None else (f"{value:g}" if isinstance(value, float) else str(value))})
    return pd.DataFrame(rows)


def _render_run(manifest: dict, L) -> None:
    with st.container(border=True, key="card_run_summary"):
        st.subheader(manifest.get("material", "-"))
        c1, c2, c3 = st.columns(3)
        c1.metric(L("specimens"), len(manifest.get("specimens", [])))
        c2.metric(L("sources"), len(manifest.get("sources", [])))
        # The full ISO-8601 timestamp ("2026-08-25T10:46:42+00:00") doesn't
        # fit a metric tile at any column width Streamlit's large metric font
        # allows -- even "date time" truncated to the minute still clips.
        # Date only in the tile; the exact time is one glance away, in the
        # column header via help text.
        created = manifest.get("created_utc", "-")
        c3.metric(
            L("ingested_utc"), created[:10] if len(created) >= 10 else created,
            help=L("full_timestamp", ts=created),
        )

    st.markdown(f"##### {L('sources_heading')}")
    sources = manifest.get("sources", [])
    if sources:
        st.dataframe(
            pd.DataFrame(
                {
                    L("col_file"): [s.get("source_file", "-") for s in sources],
                    "sha256": [s.get("sha256", "-")[:12] + "…" for s in sources],
                }
            ),
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption(L("none_recorded"))

    st.markdown(f"##### {L('specimens_heading')}")
    specimens = manifest.get("specimens", [])
    if specimens:
        labels_ = [s.get("label", "-") for s in specimens]
        st.dataframe(
            pd.DataFrame(
                {
                    "": [short_tag(lbl, i + 1) for i, lbl in enumerate(labels_)],
                    L("col_label"): labels_,
                    L("col_cycles"): [s.get("n_cycles", "-") for s in specimens],
                    L("col_json"): [s.get("json", "-") for s in specimens],
                }
            ),
            use_container_width=True, hide_index=True,
        )
    else:
        st.caption(L("none_recorded"))

    with st.expander(L("settings_used")):
        st.dataframe(_settings_table(manifest.get("config", {}), L),
                     use_container_width=True, hide_index=True)

    st.caption(L("run_footer"))


def _render_exports(ws: Workspace, manifest: dict, L) -> None:
    material = manifest.get("material", "")
    reports_dir = ws.root / "reports"
    xlsx_path = reports_dir / f"{slugify(material)}.xlsx"
    xlsx_de_path = reports_dir / f"{slugify(material)}_de.xlsx"
    html_path = reports_dir / f"{slugify(material)}.html"
    with st.container(border=True, key="card_material_export"):
        st.markdown(f"##### {L('combined_heading')}")
        st.caption(L("combined_caption"))
        if xlsx_path.exists():
            st.code(f"{xlsx_path}\n{xlsx_de_path}\n{html_path}", language=None)
        else:
            st.caption(L("not_built_material"))
        if st.button(L("rebuild_now"), icon=":material/refresh:", key="rebuild_material_export"):
            result = with_utm_animation(
                L("rebuilding"), lambda: export_material(ws, material)
            )
            if result["xlsx"]:
                st.success(L("rebuilt_material", material=material))
            else:
                st.warning(L("no_indexed_material", material=material))

    overview_path = ws.root / "reports" / "_Overview.html"
    with st.container(border=True, key="card_overview_export"):
        st.markdown(f"##### {L('overview_heading')}")
        st.caption(L("overview_caption"))
        if overview_path.exists():
            st.code(str(overview_path), language=None)
        else:
            st.caption(L("not_built"))
        if st.button(L("rebuild_now"), icon=":material/refresh:", key="rebuild_overview"):
            result = with_utm_animation(L("rebuilding"), lambda: build_overview(ws))
            if result:
                st.success(L("rebuilt_overview"))
            else:
                st.warning(L("no_indexed_workspace"))


def _render_activity(ws: Workspace, L) -> None:
    entries = audit.list_entries(ws, limit=15)
    if not entries:
        st.info(L("no_activity"))
        return
    st.caption(L("activity_caption"))
    st.dataframe(
        pd.DataFrame({
            L("col_time"): [e.get("timestamp_utc", "-") for e in entries],
            L("col_user"): [e.get("user", "-") for e in entries],
            L("col_host"): [e.get("host", "-") for e in entries],
            L("col_material"): [e.get("material", "-") for e in entries],
            L("col_specimens"): [len(e.get("specimens", [])) for e in entries],
            L("col_skipped"): [len(e.get("skipped", [])) for e in entries],
            L("col_run"): [e.get("run_dir", "-") for e in entries],
        }),
        use_container_width=True, hide_index=True,
    )


def _manifest_config(manifest: dict) -> Config:
    """The Config a run was saved with, ignoring any key this version of the
    engine no longer has."""
    known = {f.name for f in dataclasses.fields(Config)}
    return Config(**{k: v for k, v in (manifest.get("config") or {}).items() if k in known})


def _run_date(run_dir: Path) -> datetime | None:
    """The date in a run folder's name (<material>_YYYY-MM-DD[-NNN]), so a
    re-analysis lands back in that same folder instead of today's."""
    m = re.search(r"_(\d{4}-\d{2}-\d{2})(?:-\d{3})?$", run_dir.name)
    return datetime.strptime(m.group(1), "%Y-%m-%d") if m else None


def reanalyze_all(ws: Workspace) -> tuple[int, list[str], list[tuple[str, str]]]:
    """Re-run every saved run from its archived originals, with the settings
    it was saved with and on its own date, so each run is rewritten in its
    own folder (same sources, same settings, same day = same run; see
    persistence.resolve_run_dir). Returns (runs done, runs skipped for
    lack of an archived original, runs that failed with the reason)."""
    done, skipped, failed = 0, [], []
    for manifest_path in sorted(ws.processed.glob("*/run.json")):
        run_dir = manifest_path.parent
        try:
            manifest = read_json(manifest_path)
            found, missing = _reanalyze_sources(ws, manifest)
            if not found or missing:
                skipped.append(run_dir.name)
                continue
            result = ingest(
                found, ws, material=manifest.get("material", ""),
                cfg=_manifest_config(manifest), when=_run_date(run_dir),
                archive_originals=True, write_reports=_has_run_reports(run_dir),
                label_stems=_reanalyze_label_stems(ws, manifest, run_dir),
                allow_same_content=True,
            )
            _prune_stale_specimens(ws, result)
            done += 1
        except Exception as exc:  # one broken run must not stop the rest
            failed.append((run_dir.name, str(exc)))
    return done, skipped, failed


def _render_administration(ws: Workspace, L) -> None:
    with st.container(border=True, key="card_reanalyze_all"):
        st.markdown(f"##### {L('reanalyze_all_heading')}")
        st.caption(L("reanalyze_all_caption"))
        confirm = st.checkbox(L("reanalyze_all_confirm"), key="cfg_reanalyze_all_confirm")
        if st.button(
            L("reanalyze_all_button"), icon=":material/refresh:", key="reanalyze_all_btn",
            disabled=not confirm,
        ):
            done, skipped, failed = with_utm_animation(
                L("reanalyzing"), lambda: reanalyze_all(ws),
            )
            st.success(L("reanalyze_all_done", n=done))
            if skipped:
                st.warning(L("reanalyze_all_skipped", names=", ".join(skipped)))
            for name, why in failed:
                st.error(L("reanalyze_all_failed", name=name, why=why))
    with st.container(border=True, key="card_index"):
        st.markdown(f"##### {L('index_heading')}")
        st.caption(L("index_caption"))
        if st.button(L("reindex"), icon=":material/refresh:", key="reindex_from_disk"):
            count = with_utm_animation(L("reindexing"), lambda: knowledge_base.rebuild(ws))
            st.success(L("reindexed", n=count))



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


_ARCHIVE_CODE = re.compile(r"^[0-9a-f]{12}_")


def _stem_of(source_file: str) -> str:
    """File-name stem of a stored source path, whichever system wrote it: a
    Windows path keeps its backslashes when read anywhere else."""
    return (PureWindowsPath if "\\" in source_file else Path)(source_file).stem


def _reanalyze_label_stems(
    ws: Workspace, manifest: dict, run_dir: Path | None = None,
) -> dict[str, str]:
    """Maps each re-analysed source's resolved archive path to the filename
    stem its ORIGINAL ingest used, for ingest()'s own `label_stems` param.

    Without this, re-analysing re-derives every specimen's label from the
    ARCHIVE copy's own filename (`Raw exports/<sha12>_<slugified-name>.xlsx`)
    instead of the name the file actually had at first ingest -- a
    different label means a different specimen ID (persistence.specimen_id
    hashes source content + label + material), which means re-analysing
    silently adds a second, duplicate specimen to the index instead of
    updating the original in place. checked in the running app: exactly this, as two
    copies of the same specimen in Results, one under the plain original
    name and one under "<hash>_Slugified-Original-Name_S1". See
    core.load_tests' own docstring for the full mechanism.

    The stem is read from the run's own specimen records (their labels), not
    from the manifest's source path: after one re-analysis that path is the
    ARCHIVE copy, and taking the name from it again gave every specimen a new
    "<sha12>_..." label -- a second copy of the same specimens in the same
    run folder on the next re-analysis. Where a run holds both, the label
    without the archive code is the original and wins.
    """
    from_records: dict[str, list[str]] = {}
    for _json_path, spec in _run_specimens(run_dir):
        label, sha = spec.get("label"), spec.get("source_sha256")
        if not label or not sha:
            continue
        stem = re.sub(r"_S\d+$", "", label) if spec.get("source_format") == "series" else label
        from_records.setdefault(sha, []).append(stem)

    stems: dict[str, str] = {}
    for source in manifest.get("sources", []):
        rel = source.get("raw_input_path")
        if not rel:
            continue
        path = ws.root / rel
        if not path.exists():
            continue
        candidates = from_records.get(source.get("sha256"), [])
        original = [c for c in candidates if not _ARCHIVE_CODE.match(c)]
        stems[str(path.resolve())] = (
            original[0] if original else candidates[0] if candidates
            else _stem_of(source.get("source_file", ""))
        )
    return stems


def _run_specimens(run_dir: Path | None) -> list[tuple[Path, dict]]:
    """(json path, specimen block) of every specimen record in a run folder."""
    out: list[tuple[Path, dict]] = []
    if run_dir is None or not run_dir.exists():
        return out
    for json_path in sorted(run_dir.glob("*.json")):
        if json_path.name == "run.json" or json_path.name.endswith(".curve.json"):
            continue
        try:
            payload = read_json(json_path)
        except (OSError, ValueError):
            continue
        if "specimen" in payload:
            out.append((json_path, payload["specimen"]))
    return out


def _reanalyze_one(ws: Workspace, manifest: dict, run_dir: Path | None, found, material, cfg):
    """Recalculate one run and put the result in its place. Changed settings
    give the new result its own folder first (resolve_run_dir); once that
    succeeded, the old run is deleted and the new folder takes its name, so
    the material never holds the same specimens twice."""
    result = ingest(
        found, ws, material=material, cfg=cfg,
        when=_run_date(run_dir) if run_dir is not None else None,
        archive_originals=True, write_reports=_has_run_reports(run_dir),
        label_stems=_reanalyze_label_stems(ws, manifest, run_dir),
        allow_same_content=True,
    )
    _prune_stale_specimens(ws, result)
    if result.specimens and run_dir is not None and result.run_dir.resolve() != run_dir.resolve():
        delete_run(ws, run_dir)
        try:
            result.run_dir.rename(run_dir)
            result.run_dir = run_dir
        except OSError:
            pass  # OneDrive holding the folder: the result keeps its own name
        knowledge_base.rebuild(ws)
        export_material(ws, result.material)
    return result


def _has_run_reports(run_dir: Path | None) -> bool:
    """Whether a run was saved with the per-specimen Excel/CSV/HTML files, so a
    re-analysis keeps them up to date rather than leaving stale copies."""
    return bool(run_dir and run_dir.exists() and any(run_dir.glob("*.csv")))


def _prune_stale_specimens(ws: Workspace, result) -> int:
    """After a re-analysis wrote a run, remove any other specimen in that
    same folder that came from the same source file but was not re-written:
    a leftover copy under an older label. Returns how many were removed."""
    keep = {s.specimen_id for s in result.specimens}
    shas = {s.payload["specimen"].get("source_sha256") for s in result.specimens}
    removed = 0
    for json_path, spec in _run_specimens(result.run_dir):
        if spec.get("source_sha256") in shas and spec.get("specimen_id") not in keep:
            stem = json_path.name[: -len(".json")]
            for suffix in (".json", ".curve.json", ".csv", ".xlsx", ".html"):
                (json_path.parent / f"{stem}{suffix}").unlink(missing_ok=True)
            removed += 1
    if removed:
        knowledge_base.rebuild(ws)
        export_material(ws, result.material)
    return removed


def _render_reanalyze(ws: Workspace, manifest: dict, L, run_dir: Path | None = None) -> None:
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

    st.caption(L("reanalyze_caption"))
    if not found:
        st.caption(L("reanalyze_unavailable"))
        return
    if missing:
        st.warning(L(
            "reanalyze_missing_warning", n=len(missing), total=total,
            names=", ".join(missing),
        ))

    cfg = config_form()
    st.caption(L("reanalyze_overwrite_note"))
    confirm = st.checkbox(
        L("reanalyze_confirm"),
        key="cfg_reanalyze_confirm",
    )
    if st.button(
        L("reanalyze_now"), icon=":material/refresh:", key="cfg_reanalyze_btn",
        disabled=not confirm,
    ):
        result = with_utm_animation(
            L("reanalyzing"),
            lambda: _reanalyze_one(ws, manifest, run_dir, found, material, cfg),
        )
        # Shown after the rerun (the run picker may now point at a renamed
        # folder), at the top of the page.
        st.session_state["cfg_flash"] = [
            L("reanalyzed_success", run_dir=Path(result.run_dir).name, n=len(result.specimens)),
            *(L("skipped_warning", name=name, why=why) for name, why in result.skipped),
        ]
        st.session_state.pop("cfg_run", None)
        st.rerun()
