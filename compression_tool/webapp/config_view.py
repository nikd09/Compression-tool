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
from .common import config_form, dashboard_lang, short_tag, with_utm_animation

# EN/DE strings for this page's own chrome. Threaded through every helper as
# an `L` closure, same pattern as overview_view.py/materials_view.py/
# compare_view.py -- one shared sidebar toggle (app.py), not a separate one
# per page (see common.language_picker's own docstring for why).
_T = {
    "header": {"en": "Config", "de": "Konfiguration"},
    "subtitle": {"en": "What a run was actually ingested with, traced back per run, not the app's current form defaults.",
        "de": "Womit ein Lauf tatsächlich eingelesen wurde, je Lauf zurückverfolgt, nicht die aktuellen Formularvorgaben der App."},
    "run": {"en": "Run", "de": "Lauf"},
    "tab_run": {"en": "Run", "de": "Lauf"},
    "tab_reanalyze": {"en": "Re-analyse", "de": "Neu analysieren"},
    "tab_exports": {"en": "Exports", "de": "Exporte"},
    "tab_activity": {"en": "Activity", "de": "Aktivität"},
    "tab_admin": {"en": "Administration", "de": "Verwaltung"},
    "no_data": {"en": "Nothing ingested into this workspace yet - use Ingest first.",
        "de": "In diesen Workspace wurde noch nichts eingelesen - zuerst unter „Ingest“ eine Datei hinzufügen."},
    # -- Run tab --
    "specimens": {"en": "Specimens", "de": "Proben"},
    "sources": {"en": "Sources", "de": "Quellen"},
    "ingested_utc": {"en": "Ingested (UTC)", "de": "Eingelesen (UTC)"},
    "full_timestamp": {"en": "Full timestamp: {ts}", "de": "Vollständiger Zeitstempel: {ts}"},
    "sources_heading": {"en": "Sources", "de": "Quellen"},
    "none_recorded": {"en": "None recorded.", "de": "Keine erfasst."},
    "specimens_heading": {"en": "Specimens", "de": "Proben"},
    "col_file": {"en": "File", "de": "Datei"},
    "col_label": {"en": "Label", "de": "Bezeichnung"},
    "col_cycles": {"en": "Cycles", "de": "Zyklen"},
    "col_json": {"en": "JSON", "de": "JSON"},
    "settings_used": {"en": "Settings this run used", "de": "Von diesem Lauf verwendete Einstellungen"},
    "full_manifest": {"en": "Full manifest", "de": "Vollständiges Manifest"},
    "run_footer": {
        "en": "This is what the run was actually ingested with, not the app's current "
              "form defaults. The two can differ once someone changes a threshold on "
              "the Ingest tab for a later run.",
        "de": "Dies ist, womit der Lauf tatsächlich eingelesen wurde, nicht die "
              "aktuellen Formularvorgaben der App. Beides kann voneinander abweichen, "
              "sobald jemand für einen späteren Lauf einen Schwellenwert im "
              "Ingest-Tab ändert."},
    # -- Exports tab --
    "combined_heading": {"en": "Combined across every run of this material",
        "de": "Zusammengeführt über alle Läufe dieses Materials"},
    "combined_caption": {
        "en": "Two workbooks (English and German -- Excel has no live "
              "language switch, so it's two files, not one) and one "
              "standalone dashboard (open the .html file directly in a "
              "browser, no server needed, its own EN/DE toggle built in) "
              "covering every specimen ever ingested for this material, not "
              "just this run. Regenerated automatically on every Commit; "
              "rebuild manually below if this predates that or looks stale.",
        "de": "Zwei Arbeitsmappen (Englisch und Deutsch -- Excel hat keinen "
              "Sprachwechsel zur Laufzeit, daher zwei Dateien statt einer) "
              "und ein eigenständiges Dashboard (die .html-Datei "
              "direkt im Browser öffnen, kein Server nötig, mit eigenem "
              "EN/DE-Umschalter) für jede jemals für "
              "dieses Material eingelesene Probe, nicht nur diesen Lauf. Wird bei "
              "jedem Commit automatisch neu erzeugt; unten manuell neu erstellen, "
              "falls dies älter ist oder veraltet wirkt."},
    "not_built_material": {"en": "Not built yet for this material.", "de": "Für dieses Material noch nicht erstellt."},
    "rebuild_now": {"en": "Rebuild now", "de": "Jetzt neu erstellen"},
    "rebuilding": {"en": "Rebuilding...", "de": "Wird neu erstellt…"},
    "rebuilt_material": {"en": "Rebuilt from every indexed specimen of {material!r}.",
        "de": "Neu erstellt aus allen indizierten Proben von {material!r}."},
    "no_indexed_material": {"en": "No indexed specimens found for {material!r}.",
        "de": "Keine indizierten Proben für {material!r} gefunden."},
    "overview_heading": {"en": "Overview across every material", "de": "Übersicht über alle Materialien"},
    "overview_caption": {
        "en": "One page listing every material in this workspace, with its "
              "specimen/run counts, peak stress and thickness, and a link "
              "into each material's own report. Open the .html file directly "
              "in a browser, no server needed. Regenerated automatically on "
              "every Commit, from any material.",
        "de": "Eine Seite, die jedes Material in diesem Workspace auflistet, mit "
              "Proben-/Laufzahlen, Spitzenspannung und Dicke, sowie einem Link zum "
              "jeweiligen Materialbericht. Die .html-Datei direkt im Browser "
              "öffnen, kein Server nötig. Wird bei jedem Commit, von jedem "
              "Material aus, automatisch neu erzeugt."},
    "not_built": {"en": "Not built yet.", "de": "Noch nicht erstellt."},
    "rebuilt_overview": {"en": "Rebuilt from every indexed material.", "de": "Aus allen indizierten Materialien neu erstellt."},
    "no_indexed_workspace": {"en": "No indexed specimens found in this workspace.",
        "de": "Keine indizierten Proben in diesem Workspace gefunden."},
    # -- Activity tab --
    "no_activity": {"en": "No activity recorded yet.", "de": "Noch keine Aktivität erfasst."},
    "activity_caption": {
        "en": "Who ingested what, and when: one record per Commit, across the "
              "whole workspace, not scoped to one run. The 15 most recent; every "
              "record ever written is a small JSON file under audit/, or "
              "`compression_tool audit` on the CLI.",
        "de": "Wer was wann eingelesen hat: ein Eintrag je Commit, über den "
              "gesamten Workspace, nicht auf einen Lauf beschränkt. Die 15 "
              "neuesten; jeder jemals geschriebene Eintrag ist eine kleine "
              "JSON-Datei unter audit/, oder `compression_tool audit` auf der "
              "Kommandozeile."},
    "col_time": {"en": "Time (UTC)", "de": "Zeit (UTC)"},
    "col_user": {"en": "User", "de": "Benutzer"},
    "col_host": {"en": "Host", "de": "Host"},
    "col_material": {"en": "Material", "de": "Material"},
    "col_specimens": {"en": "Specimens", "de": "Proben"},
    "col_skipped": {"en": "Skipped", "de": "Übersprungen"},
    "col_run": {"en": "Run", "de": "Lauf"},
    # -- Administration tab --
    "index_heading": {"en": "Index", "de": "Index"},
    "index_caption": {
        "en": "The database every tab reads from, built from the JSON records "
              "under Records/. It only ever grows or updates when this app "
              "writes to it - if a record's file was deleted outside the app "
              "(Explorer, the shared drive) the index still lists it until "
              "reindexed, and a tab that then tries to open it will show an "
              "error instead of the material. Rebuilding is always safe: the "
              "JSON records are the source of truth, the index is only ever "
              "derived from them.",
        "de": "Die Datenbank, aus der jeder Tab liest, aufgebaut aus den "
              "JSON-Datensätzen unter Records/. Sie wächst oder aktualisiert sich "
              "nur, wenn diese App hineinschreibt - wurde die Datei eines "
              "Datensatzes außerhalb der App gelöscht (Explorer, das gemeinsame "
              "Laufwerk), listet der Index ihn bis zur Neuindizierung weiter, und "
              "ein Tab, der ihn dann öffnen will, zeigt statt des Materials einen "
              "Fehler. Neu erstellen ist immer sicher: die JSON-Datensätze sind "
              "die maßgebliche Quelle, der Index wird nur aus ihnen abgeleitet."},
    "reindex": {"en": "Reindex from disk", "de": "Von der Festplatte neu indizieren"},
    "reindexing": {"en": "Reindexing...", "de": "Wird neu indiziert…"},
    "reindexed": {"en": "Reindexed {n} specimen record(s) from disk.",
        "de": "{n} Probendatensatz/-sätze von der Festplatte neu indiziert."},
    # -- Re-analyse tab --
    "reanalyze_caption": {
        "en": "Re-run this run's archived source file(s) with different "
              "thresholds, without re-uploading anything. Change a value below "
              "only if this run's numbers look wrong and a different setting "
              "would fix it -- most runs never need this.",
        "de": "Die archivierte(n) Quelldatei(en) dieses Laufs mit anderen "
              "Schwellenwerten erneut auswerten, ohne etwas neu hochzuladen. "
              "Einen Wert unten nur ändern, wenn die Zahlen dieses Laufs falsch "
              "wirken und eine andere Einstellung das beheben würde -- die "
              "meisten Läufe brauchen das nie."},
    "reanalyze_unavailable": {
        "en": "Not available - none of this run's source files were "
              "archived (ingested with 'Archive a copy of the uploaded "
              "file' unchecked on the Ingest tab), so there is nothing on "
              "disk to re-run this from.",
        "de": "Nicht verfügbar - keine der Quelldateien dieses Laufs wurde "
              "archiviert (eingelesen mit deaktiviertem „Eine Kopie der "
              "hochgeladenen Datei archivieren“ im Ingest-Tab), daher gibt es "
              "nichts auf der Festplatte, worauf das aufbauen könnte."},
    "reanalyze_missing_warning": {
        "en": "{n} of {total} source file(s) for this run are missing from the "
              "archive and will be skipped: {names}",
        "de": "{n} von {total} Quelldatei(en) dieses Laufs fehlen im Archiv und "
              "werden übersprungen: {names}"},
    "hold_at_peak": {"en": "Test has a hold at peak", "de": "Prüfung hat ein Halten am Spitzenwert"},
    "gauge_confirmed": {"en": "Gauge length confirmed", "de": "Messlänge bestätigt"},
    "gauge_confirmed_help": {
        "en": "Check this only once someone has verified the displacement "
              "channel spans exactly this specimen's h0. Left unchecked, strain "
              "and modulus stay provisional in the re-analysed record.",
        "de": "Nur aktivieren, wenn jemand bestätigt hat, dass der Wegkanal "
              "genau die h0 dieser Probe erfasst. Unmarkiert bleiben Dehnung "
              "und Modul im neu analysierten Datensatz vorläufig."},
    "reanalyze_overwrite_note": {
        "en": "This overwrites this run's stored numbers in place if the settings "
              "below fingerprint the same as today's, or creates a new run "
              "alongside it if they don't -- either way, nothing here touches the "
              "original uploaded export.",
        "de": "Dies überschreibt die gespeicherten Werte dieses Laufs direkt, "
              "falls die Einstellungen unten denselben Fingerabdruck wie heute "
              "ergeben, oder legt andernfalls einen neuen Lauf daneben an -- in "
              "beiden Fällen bleibt der ursprünglich hochgeladene Export "
              "unangetastet."},
    "reanalyze_confirm": {"en": "I understand this replaces this run's stored analysis",
        "de": "Mir ist klar, dass dies die gespeicherte Analyse dieses Laufs ersetzt"},
    "reanalyze_now": {"en": "Re-analyse now", "de": "Jetzt neu analysieren"},
    "reanalyzing": {"en": "Re-analysing...", "de": "Wird neu analysiert…"},
    "reanalyzed_success": {"en": "Re-analysed into {run_dir} - {n} specimen(s).",
        "de": "Neu analysiert nach {run_dir} - {n} Probe(n)."},
    "skipped_warning": {"en": "Skipped {name}: {why}", "de": "{name} übersprungen: {why}"},
    # -- Admin access --
    "admin_access_heading": {"en": "Admin access", "de": "Admin-Zugriff"},
    "admin_not_configured": {
        "en": "Nobody has restricted this yet - every visitor, including "
              "you ({me}), can currently rename or delete a material from "
              "the Materials tab. Claim admin access to restrict Rename "
              "and Delete to specific people from here on.",
        "de": "Das wurde noch von niemandem eingeschränkt - jeder Besucher, "
              "auch Sie ({me}), kann aktuell im Materials-Tab ein Material "
              "umbenennen oder löschen. Admin-Zugriff beanspruchen, um "
              "Umbenennen und Löschen ab jetzt auf bestimmte Personen zu "
              "beschränken."},
    "claim_admin": {"en": "Claim admin access for myself", "de": "Admin-Zugriff für mich beanspruchen"},
    "admin_is_admin": {
        "en": "Signed in as {me} - listed as an admin. Rename and Delete "
              "are visible on the Materials tab. Add or remove people below; "
              "this only checks the OS username the app is running under, "
              "the same identity Config's audit log already records.",
        "de": "Angemeldet als {me} - als Admin gelistet. Umbenennen und "
              "Löschen sind im Materials-Tab sichtbar. Unten Personen "
              "hinzufügen oder entfernen; dies prüft nur den "
              "Betriebssystem-Benutzernamen, unter dem die App läuft, dieselbe "
              "Identität, die das Aktivitätsprotokoll in Config bereits "
              "erfasst."},
    "col_admin": {"en": "Admin", "de": "Admin"},
    "add_admin_placeholder": {"en": "Add admin (Windows/OS username)", "de": "Admin hinzufügen (Windows-/OS-Benutzername)"},
    "add_admin_example": {"en": "e.g. n7215177", "de": "z. B. n7215177"},
    "add": {"en": "Add", "de": "Hinzufügen"},
    "remove_admin_label": {"en": "Remove an admin", "de": "Einen Admin entfernen"},
    "remove": {"en": "Remove", "de": "Entfernen"},
    "admin_not_admin": {
        "en": "Signed in as {me} - not on the admin list ({admins}). "
              "Rename and Delete are hidden on the Materials tab. Ask one of "
              "them to add you above, or edit admins.json directly at the "
              "workspace root.",
        "de": "Angemeldet als {me} - nicht auf der Admin-Liste ({admins}). "
              "Umbenennen und Löschen sind im Materials-Tab ausgeblendet. Eine "
              "der gelisteten Personen bitten, Sie oben hinzuzufügen, oder "
              "admins.json direkt im Workspace-Stammverzeichnis bearbeiten."},
}


def render(ws: Workspace) -> None:
    lang = dashboard_lang()

    def L(key: str, **kw) -> str:
        s = _T[key][lang]
        return s.format(**kw) if kw else s

    st.header(L("header"))
    st.caption(L("subtitle"))

    manifests = sorted(ws.processed.glob("*/run.json")) if ws.processed.exists() else []

    manifest = None
    if manifests:
        # Rendered above the tabs, not inside one -- every tab that needs
        # "which run" reads this same selection, so switching tabs never
        # re-asks for it.
        labels = {m: f"{m.parent.name}" for m in manifests}
        chosen = st.selectbox(L("run"), manifests, format_func=lambda m: labels[m])
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
    with tab_reanalyze:
        _render_reanalyze(ws, manifest, L)
    with tab_exports:
        _render_exports(ws, manifest, L)
    with tab_activity:
        _render_activity(ws, L)
    with tab_admin:
        _render_administration(ws, L)


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
        st.json(manifest.get("config", {}), expanded=False)
    with st.expander(L("full_manifest")):
        st.json(manifest)

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


def _render_administration(ws: Workspace, L) -> None:
    with st.container(border=True, key="card_index"):
        st.markdown(f"##### {L('index_heading')}")
        st.caption(L("index_caption"))
        if st.button(L("reindex"), icon=":material/refresh:", key="reindex_from_disk"):
            count = with_utm_animation(L("reindexing"), lambda: knowledge_base.rebuild(ws))
            st.success(L("reindexed", n=count))

    _render_admin_access(ws, L)


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


def _reanalyze_label_stems(ws: Workspace, manifest: dict) -> dict[str, str]:
    """Maps each re-analysed source's resolved archive path to the filename
    stem its ORIGINAL ingest used, for ingest()'s own `label_stems` param.

    Without this, re-analysing re-derives every specimen's label from the
    ARCHIVE copy's own filename (`Raw exports/<sha12>_<slugified-name>.xlsx`)
    instead of the name the file actually had at first ingest -- a
    different label means a different specimen ID (persistence.specimen_id
    hashes source content + label + material), which means re-analysing
    silently adds a second, duplicate specimen to the index instead of
    updating the original in place. Confirmed live: exactly this, as two
    copies of the same specimen in Results, one under the plain original
    name and one under "<hash>_Slugified-Original-Name_S1". See
    core.load_tests' own docstring for the full mechanism.
    """
    stems: dict[str, str] = {}
    for source in manifest.get("sources", []):
        rel = source.get("raw_input_path")
        if not rel:
            continue
        path = ws.root / rel
        if path.exists():
            stems[str(path.resolve())] = Path(source.get("source_file", "")).stem
    return stems


def _render_reanalyze(ws: Workspace, manifest: dict, L) -> None:
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

    detect_holds = st.checkbox(
        L("hold_at_peak"), value=True, key="cfg_reanalyze_holds",
    )
    cfg = config_form(detect_holds)
    gauge_confirmed = st.checkbox(
        L("gauge_confirmed"), key="cfg_reanalyze_gauge",
        help=L("gauge_confirmed_help"),
    )
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
            lambda: ingest(
                found, ws, material=material, cfg=cfg,
                gauge_length_confirmed=gauge_confirmed,
                archive_originals=True, write_reports=True,
                label_stems=_reanalyze_label_stems(ws, manifest),
            ),
        )
        st.success(L("reanalyzed_success", run_dir=result.run_dir, n=len(result.specimens)))
        for name, why in result.skipped:
            st.warning(L("skipped_warning", name=name, why=why))
        st.rerun()


def _render_admin_access(ws: Workspace, L) -> None:
    """Who may Rename or Delete a material from the Materials tab -- see
    permissions.py for what this does and does not actually enforce (a
    shared, hand-editable allowlist keyed on the OS username, not a login
    system)."""
    with st.container(border=True, key="card_admin_access"):
        st.markdown(f"##### {L('admin_access_heading')}")
        me = permissions.current_user()
        if not permissions.admins_configured(ws):
            st.caption(L("admin_not_configured", me=me))
            if st.button(L("claim_admin"), icon=":material/shield:"):
                permissions.claim_admin(ws)
                st.rerun()
            return

        admins = permissions.load_admins(ws)
        is_admin = permissions.is_admin(ws)
        if is_admin:
            st.caption(L("admin_is_admin", me=me))
            st.dataframe(
                pd.DataFrame({L("col_admin"): admins}), use_container_width=True, hide_index=True,
            )
            c1, c2 = st.columns([3, 1])
            with c1:
                new_name = st.text_input(
                    L("add_admin_placeholder"), key="cfg_add_admin",
                    label_visibility="collapsed", placeholder=L("add_admin_example"),
                )
            with c2:
                if st.button(L("add"), key="cfg_add_admin_btn") and new_name.strip():
                    permissions.add_admin(ws, new_name.strip())
                    st.rerun()
            removable = [a for a in admins if a.casefold() != me.casefold()]
            if removable:
                to_remove = st.selectbox(
                    L("remove_admin_label"), [""] + removable, key="cfg_remove_admin",
                )
                if to_remove and st.button(L("remove"), key="cfg_remove_admin_btn"):
                    permissions.remove_admin(ws, to_remove)
                    st.rerun()
        else:
            st.caption(L("admin_not_admin", me=me, admins=", ".join(admins)))
