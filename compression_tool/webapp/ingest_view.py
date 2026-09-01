"""Ingest: upload exports, preview what the engine sees before anything is
written, then commit. Mirrors `compression-tool preview` / `ingest` on the
CLI -- this view calls the exact same two functions, nothing is reimplemented."""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional

import streamlit as st
import streamlit.components.v1 as components

from ..core import Config
from ..dashboard_data import MAX_SPECIMENS, build_dashboard_data
from ..material_registry import load_materials
from ..persistence import Workspace
from ..pipeline import ingest, preview, preview_dashboard_data
from .common import config_form, dashboard_lang, inject_dashboard_lang, with_utm_animation

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

# EN/DE strings for this page's own chrome. Threaded through every helper as
# an `L` closure reading the one shared sidebar toggle (common.dashboard_lang),
# same pattern as every other translated view.
_T = {
    "header": {"en": "Ingest", "de": "Einlesen"},
    "subtitle": {"en": "Upload one or more exports of the same series, look before committing, then archive and index them.",
        "de": "Eine oder mehrere Exportdateien derselben Serie hochladen, vor dem Speichern prüfen, dann archivieren und indizieren."},
    "step1": {"en": "Upload", "de": "Hochladen"},
    "uploader_label": {"en": "Compression test export(s)", "de": "Druckversuchs-Exportdatei(en)"},
    "uploader_help": {"en": "One or more .xlsx exports of the same series. Nothing is written to the workspace until Commit.",
        "de": "Eine oder mehrere .xlsx-Exporte derselben Serie. Es wird nichts in den Workspace geschrieben, bis „Speichern“ gedrückt wird."},
    "new_material": {"en": "+ Add new material…", "de": "+ Neues Material hinzufügen…"},
    "material_label": {"en": "Material", "de": "Material"},
    "material_placeholder_empty": {"en": "e.g. PEEK-GF30, the first material in this workspace",
        "de": "z. B. PEEK-GF30, das erste Material in diesem Workspace"},
    "material_help_empty": {"en": "Every material typed here becomes a pickable option next time, so it never has to be retyped or matched exactly again.",
        "de": "Jedes hier eingetippte Material wird beim nächsten Mal auswählbar, sodass es nie wieder exakt eingetippt werden muss."},
    "material_placeholder_pick": {"en": "Pick a material, or add a new one", "de": "Ein Material auswählen, oder ein neues hinzufügen"},
    "material_help_pick": {
        "en": "Picking from this list, rather than retyping the name, is "
              "what keeps \"SteelMesh\" and \"Steel Mesh\" from silently becoming "
              "two materials that never compare against each other.",
        "de": "Aus dieser Liste auszuwählen, statt den Namen neu einzutippen, "
              "verhindert, dass „SteelMesh“ und „Steel Mesh“ stillschweigend zu "
              "zwei Materialien werden, die nie miteinander verglichen werden."},
    "new_material_name": {"en": "New material name", "de": "Name des neuen Materials"},
    "new_material_placeholder": {"en": "e.g. PEEK-GF30", "de": "z. B. PEEK-GF30"},
    "hold_at_peak": {"en": "Test has a hold at peak", "de": "Prüfung hat ein Halten am Spitzenwert"},
    "hold_at_peak_help": {
        "en": "Uncheck for a fast-cycling test with no programmed dwell. "
              "A short cycle still spends a few samples turning around at peak "
              "stress (geometry, not a hold), and on a short enough cycle "
              "that turnaround can accidentally look long enough to be misread "
              "as a real one. Unchecking skips hold detection entirely, so "
              "every cycle reports no hold and no creep, instead of a few "
              "false ones scattered through an otherwise hold-free test.",
        "de": "Deaktivieren bei einer schnell zyklierenden Prüfung ohne "
              "programmiertes Halten. Ein kurzer Zyklus verbringt trotzdem ein "
              "paar Messpunkte mit der Umkehr bei Spitzenspannung (Geometrie, "
              "kein Halten), und bei einem ausreichend kurzen Zyklus kann diese "
              "Umkehr versehentlich lang genug wirken, um als echtes Halten "
              "fehlgedeutet zu werden. Deaktivieren überspringt die "
              "Halteerkennung vollständig, sodass jeder Zyklus kein Halten und "
              "kein Kriechen meldet, statt vereinzelter falscher Treffer in "
              "einer sonst haltefreien Prüfung."},
    "gauge_confirmed": {"en": "Gauge length confirmed", "de": "Messlänge bestätigt"},
    "gauge_confirmed_help": {
        "en": "Check this only once someone has verified the displacement "
              "channel's extensometer spans exactly this specimen's measured "
              "thickness h0, not just that h0 gives a plausible modulus. Left "
              "unchecked, strain and modulus stay provisional and carry a "
              "critical warning.",
        "de": "Nur aktivieren, wenn jemand bestätigt hat, dass der Extensometer "
              "des Wegkanals genau die gemessene Dicke h0 dieser Probe erfasst, "
              "nicht nur, dass h0 einen plausiblen Modul ergibt. Unmarkiert "
              "bleiben Dehnung und Modul vorläufig und tragen einen kritischen "
              "Hinweis."},
    "filename_warning": {
        "en": "'{material}' looks like the export's file name, not a material "
              "code. A short code (e.g. 'T050LR1') reads far better as a "
              "Materials card and in Compare's legend - the file name itself "
              "is already kept, in full, on every specimen record. This can "
              "still be fixed after Commit, from the Materials tab.",
        "de": "„{material}“ sieht wie der Dateiname des Exports aus, nicht wie "
              "ein Materialcode. Ein kurzer Code (z. B. „T050LR1“) liest sich "
              "als Materials-Karte und in der Compare-Legende deutlich besser "
              "- der Dateiname selbst wird bereits vollständig in jedem "
              "Probendatensatz aufbewahrt. Das lässt sich auch nach dem "
              "Speichern noch im Materials-Tab korrigieren."},
    "diff_materials_expander": {"en": "Different materials in this batch? ({n} files uploaded)",
        "de": "Unterschiedliche Materialien in diesem Stapel? ({n} Dateien hochgeladen)"},
    "diff_materials_caption": {
        "en": "Each file defaults to the Material picked above. Change a file "
              "here only if IT specifically belongs to a different material -- "
              "e.g. two exports for two different materials uploaded together, "
              "which would otherwise be silently combined into one material.",
        "de": "Jede Datei verwendet zunächst das oben gewählte Material. Eine "
              "Datei hier nur ändern, wenn SIE speziell zu einem anderen "
              "Material gehört -- z. B. zwei Exporte für zwei verschiedene "
              "Materialien, die zusammen hochgeladen wurden und sonst "
              "stillschweigend zu einem Material zusammengeführt würden."},
    "same_as_above": {"en": "(same as above)", "de": "(wie oben)"},
    "step2": {"en": "Thresholds", "de": "Schwellenwerte"},
    "optional": {"en": "Optional.", "de": "Optional."},
    "no_upload_info": {"en": "Upload one or more exports above to preview them.",
        "de": "Oben eine oder mehrere Exportdateien hochladen, um sie in der Vorschau zu sehen."},
    "step3": {"en": "Preview", "de": "Vorschau"},
    "step3_sub": {"en": "Check the files parse, then open the full interactive dashboard in a new tab, before anything is written.",
        "de": "Prüfen, ob die Dateien sich einlesen lassen, dann das vollständige interaktive Dashboard in einem neuen Tab öffnen, bevor etwas geschrieben wird."},
    "run_preview": {"en": "Run preview", "de": "Vorschau ausführen"},
    "analysing": {"en": "Analysing…", "de": "Wird analysiert…"},
    "skipped": {"en": "Skipped {name}: {why}", "de": "{name} übersprungen: {why}"},
    "nothing_to_chart": {"en": "Nothing to chart: every file failed to analyse, or had no cycles.",
        "de": "Nichts darzustellen: jede Datei ließ sich nicht analysieren oder hatte keine Zyklen."},
    "truncated_caption": {
        "en": "Showing the {max} most recent of {total} specimens; the "
              "dashboard's colour palette has a hard {max}-series limit.",
        "de": "Zeigt die {max} neuesten von {total} Proben; die Farbpalette des "
              "Dashboards hat ein hartes Limit von {max} Serien."},
    "open_in_new_tab": {"en": "Open dashboard in a new tab", "de": "Dashboard in neuem Tab öffnen"},
    "step4": {"en": "Commit", "de": "Speichern"},
    "step4_sub": {"en": "Archives the raw file and writes the record - re-running the same file is a no-op.",
        "de": "Archiviert die Rohdatei und schreibt den Datensatz - dieselbe Datei erneut auszuführen bewirkt nichts."},
    "archive_checkbox": {"en": "Archive a copy of the uploaded file", "de": "Eine Kopie der hochgeladenen Datei archivieren"},
    "archive_help": {
        "en": "Copies the export into Raw exports/ before analysis, so a "
              "result can always be traced back to the exact bytes that "
              "produced it. Uncheck if you already keep your own copies "
              "elsewhere and do not want a second one on disk. The file's "
              "SHA-256 is still recorded either way, which is what a "
              "re-ingest of the same file is detected from.",
        "de": "Kopiert den Export vor der Analyse nach Raw exports/, damit ein "
              "Ergebnis immer bis zu den exakten Bytes zurückverfolgt werden "
              "kann, die es erzeugt haben. Deaktivieren, wenn bereits eigene "
              "Kopien anderswo aufbewahrt werden und keine zweite auf der "
              "Festplatte gewünscht ist. Der SHA-256-Wert der Datei wird in "
              "jedem Fall erfasst, woran ein erneutes Einlesen derselben Datei "
              "erkannt wird."},
    "write_reports_checkbox": {"en": "Write per-run Excel/CSV/HTML", "de": "Excel/CSV/HTML je Lauf schreiben"},
    "write_reports_help": {
        "en": "Writes a per-specimen and per-run Excel workbook, CSV and "
              "HTML report alongside the JSON record. Uncheck if you only "
              "open the combined report in reports/<material> (see Config) "
              "and find these per-run copies redundant. The JSON record "
              "and curve cache, which the combined report and every chart "
              "are rebuilt from, are always written either way.",
        "de": "Schreibt eine Excel-Arbeitsmappe, CSV und einen HTML-Bericht je "
              "Probe und je Lauf, zusätzlich zum JSON-Datensatz. Deaktivieren, "
              "wenn nur der zusammengeführte Bericht unter reports/<material> "
              "(siehe Config) geöffnet wird und diese Kopien je Lauf "
              "überflüssig sind. Der JSON-Datensatz und der Kurven-Cache, aus "
              "denen der zusammengeführte Bericht und jedes Diagramm neu "
              "erzeugt werden, werden in jedem Fall immer geschrieben."},
    "commit_button": {"en": "Commit to workspace", "de": "In Workspace speichern"},
    "commit_error_no_material": {"en": "Pick or type a material name above before committing.",
        "de": "Vor dem Speichern oben einen Materialnamen auswählen oder eingeben."},
    "committing": {"en": "Committing…", "de": "Wird gespeichert…"},
    "matched_existing": {
        "en": "'{typed}' matched to the existing material '{actual}' instead of creating a near-duplicate.",
        "de": "„{typed}“ wurde dem bestehenden Material „{actual}“ zugeordnet, "
              "statt ein Fast-Duplikat anzulegen."},
    "ingested_success": {"en": "Ingested {n} specimen(s) of '{material}' into {run_dir}",
        "de": "{n} Probe(n) von „{material}“ in {run_dir} eingelesen"},
}


def _step(n: int, title: str, sub: str = "") -> None:
    st.markdown(
        f'<div class="ct-step-head"><span class="ct-step">{n}</span><h3>{title}</h3></div>'
        + (f'<p class="ct-step-sub">{sub}</p>' if sub else ""),
        unsafe_allow_html=True,
    )


def _material_picker(ws: Workspace, L, *, key_suffix: str = "") -> str:
    """A picker, not a free-text box, once at least one material exists --
    "SteelMesh", "Steel Mesh" and "steel-mesh" typed on three different
    days become three materials that never compare against each other in
    Results or Compare, silently. Picking from what already exists is what
    prevents that; "+ Add new material" is the explicit, deliberate escape
    hatch for a genuinely new one. ingest() itself also normalizes a
    near-duplicate typed here to the existing entry as a second line of
    defence, so this is a nudge toward the right habit, not the only guard.

    `key_suffix` lets this be called more than once on the same rerun --
    _per_file_materials() below reuses it per uploaded file, which needs a
    distinct widget key per call or Streamlit would treat every call as the
    SAME widget instance.
    """
    materials = load_materials(ws)
    new_material = L("new_material")
    if not materials:
        return st.text_input(
            L("material_label"), key=f"ingest_material_text{key_suffix}",
            placeholder=L("material_placeholder_empty"),
            help=L("material_help_empty"),
        )
    choice = st.selectbox(
        L("material_label"), materials + [new_material], index=None,
        key=f"ingest_material_select{key_suffix}",
        placeholder=L("material_placeholder_pick"),
        help=L("material_help_pick"),
    )
    if choice == new_material:
        return st.text_input(
            L("new_material_name"), key=f"ingest_material_new{key_suffix}",
            placeholder=L("new_material_placeholder"),
            label_visibility="collapsed",
        )
    return choice or ""


def _per_file_materials(ws: Workspace, uploaded, default_material: str, L) -> dict[int, str]:
    """Optional per-file material override for a multi-file upload.

    The bug this exists to fix: Ingest used to have exactly ONE Material
    field for the whole batch, so uploading two exports meant for two
    DIFFERENT materials together silently combined them into one material
    with all specimens under a single name -- no warning, no way to split
    them apart short of deleting and re-ingesting separately.

    Only shown once more than one file is attached, and every file defaults
    to "(same as above)" -- the common case (several files, one material)
    still needs zero extra clicks. Returns {file index: material name} for
    only the files someone actually overrode; a file left on the default is
    simply absent from the dict, and the caller falls back to
    `default_material` for it.
    """
    if len(uploaded) < 2:
        return {}
    _SAME = L("same_as_above")
    new_material = L("new_material")
    materials = load_materials(ws)
    overrides: dict[int, str] = {}
    with st.expander(L("diff_materials_expander", n=len(uploaded))):
        st.caption(L("diff_materials_caption"))
        for i, f in enumerate(uploaded):
            key_base = f"ingest_pf_{i}_{f.name}_{f.size}"
            options = [_SAME, new_material] + materials
            choice = st.selectbox(f.name, options, index=0, key=f"{key_base}_sel")
            if choice == new_material:
                typed = st.text_input(
                    L("new_material_name"), key=f"{key_base}_new",
                    label_visibility="collapsed", placeholder=L("new_material_placeholder"),
                )
                if typed.strip():
                    overrides[i] = typed.strip()
            elif choice != _SAME:
                overrides[i] = choice
    return overrides


def _resolve_material_groups(
    paths: list[Path], default_material: str, overrides: dict[int, str]
) -> dict[str, list[Path]]:
    """Groups `paths` by their resolved material -- an override for that
    index if one was picked in _per_file_materials(), else
    `default_material` -- preserving first-seen order. With no overrides
    (the common case) this is just `{default_material: paths}`, identical
    to the single ingest() call this replaced."""
    groups: dict[str, list[Path]] = {}
    for i, p in enumerate(paths):
        mat = overrides.get(i, default_material).strip()
        groups.setdefault(mat, []).append(p)
    return groups


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
    paths: list[Path], cfg: Config, material: str, gauge_confirmed: bool,
    material_by_path: Optional[dict[str, str]] = None,
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
        material_by_path=material_by_path,
        gauge_length_confirmed=gauge_confirmed,
    )
    html = None
    if result["payloads"]:
        data = build_dashboard_data(result["payloads"], result["curves"])
        html = inject_dashboard_lang(_DASHBOARD_TEMPLATE_PATH.read_text(encoding="utf-8").replace(
            "/*__DATA__*/", json.dumps(data)
        ))
    return {
        "skipped": result["skipped"],
        "truncated": result["truncated"],
        "total_specimens": result["total_specimens"],
        "html": html,
    }


_OPEN_IN_NEW_TAB_HEIGHT_PX = 56


def _open_in_new_tab_button(html: str, label: str) -> None:
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


def _render_dashboard_preview(state: dict, L) -> None:
    for name, why in state["skipped"]:
        st.warning(L("skipped", name=name, why=why))
    if not state["html"]:
        st.warning(L("nothing_to_chart"))
        return
    if state["truncated"]:
        st.caption(L("truncated_caption", max=MAX_SPECIMENS, total=state["total_specimens"]))
    _open_in_new_tab_button(state["html"], L("open_in_new_tab"))


def render(ws: Workspace) -> None:
    _sweep_stale_uploads()
    lang = dashboard_lang()

    def L(key: str, **kw) -> str:
        s = _T[key][lang]
        return s.format(**kw) if kw else s

    st.header(L("header"))
    st.caption(L("subtitle"))

    _step(1, L("step1"))
    uploaded = st.file_uploader(
        L("uploader_label"), type=["xlsx"], accept_multiple_files=True,
        label_visibility="collapsed",
        help=L("uploader_help"),
    )
    c1, c2 = st.columns([2, 1])
    with c1:
        material = _material_picker(ws, L)
    with c2:
        st.markdown("<div style='height:1.6rem'></div>", unsafe_allow_html=True)
        detect_holds = st.checkbox(
            L("hold_at_peak"), value=True,
            help=L("hold_at_peak_help"),
        )
        gauge_confirmed = st.checkbox(
            L("gauge_confirmed"),
            help=L("gauge_confirmed_help"),
        )

    if uploaded and _looks_like_a_filename(material, uploaded):
        st.warning(L("filename_warning", material=material))

    per_file_material = _per_file_materials(ws, uploaded, material, L) if uploaded else {}

    st.divider()
    _step(2, L("step2"), L("optional"))
    cfg = config_form(detect_holds)

    if not uploaded:
        # Nothing attached (including "no longer attached" -- the uploader
        # was cleared): no reason to keep a copy of files that are no
        # longer in the form around on disk.
        _cleanup_upload_dir()
        st.info(L("no_upload_info"))
        return
    paths = _save_uploads(uploaded)
    material_groups = _resolve_material_groups(paths, material.strip(), per_file_material)

    st.divider()
    _step(3, L("step3"), L("step3_sub"))
    if st.button(L("run_preview"), icon=":material/visibility:"):
        # Invert material_groups (material -> its paths) back into path ->
        # material, so a multi-material batch previews with each specimen
        # correctly labelled instead of all of them under one material --
        # the same split Commit below uses, just for the look-before-you-commit
        # dashboard. None when there is only one group (the common case):
        # _build_dashboard_preview then falls back to its old single-material
        # behaviour exactly as before.
        material_by_path = (
            {str(p): mat for mat, group_paths in material_groups.items() for p in group_paths}
            if len(material_groups) > 1 else None
        )

        def _preview_and_build_dashboard() -> tuple[list[dict], dict]:
            # One click, one animation, both calls -- not a second button
            # that only appears (and still has to be clicked and waited on
            # again) once this one finishes. "Open dashboard in a new tab"
            # popping in once this is done IS the "preview finished, look
            # at the results" signal, not a separate step to notice and act on.
            rows_ = preview(paths, cfg, gauge_length_confirmed=gauge_confirmed)
            dashboard_ = _build_dashboard_preview(
                paths, cfg, material, gauge_confirmed, material_by_path=material_by_path,
            )
            return rows_, dashboard_

        rows, dashboard_state = with_utm_animation(L("analysing"), _preview_and_build_dashboard)
        st.session_state["ingest_preview_rows"] = rows
        st.session_state["ingest_preview_dashboard"] = dashboard_state

    rows = st.session_state.get("ingest_preview_rows")
    if rows:
        _render_preview_errors(rows)
    dashboard_state = st.session_state.get("ingest_preview_dashboard")
    if dashboard_state:
        _render_dashboard_preview(dashboard_state, L)

    st.divider()
    _step(4, L("step4"), L("step4_sub"))
    c1, c2 = st.columns(2)
    with c1:
        archive_originals = st.checkbox(
            L("archive_checkbox"), value=True,
            help=L("archive_help"),
        )
    with c2:
        write_reports = st.checkbox(
            L("write_reports_checkbox"), value=True,
            help=L("write_reports_help"),
        )
    if st.button(L("commit_button"), type="primary", icon=":material/save:"):
        if any(not mat for mat in material_groups):
            st.error(L("commit_error_no_material"))
        else:
            # One ingest() call PER MATERIAL GROUP, not one call for the
            # whole upload -- ingest() only ever accepts a single material
            # for everything it is given, so a batch split across materials
            # (via the "Different materials in this batch?" section above)
            # would otherwise land every specimen under just one of them.
            # With no split, material_groups is {material: paths} -- one
            # group, one call, identical to before.
            results = with_utm_animation(
                L("committing"),
                lambda: [
                    ingest(
                        group_paths, ws, material=mat, cfg=cfg,
                        gauge_length_confirmed=gauge_confirmed,
                        archive_originals=archive_originals,
                        write_reports=write_reports,
                    )
                    for mat, group_paths in material_groups.items()
                ],
            )
            # The uploaded copy has done its job -- ingest() has already
            # archived (or hashed) and read every file -- so there is
            # nothing left for it to do on disk.
            _cleanup_upload_dir()
            # zip() relies on `results` having been built in the exact same
            # order as material_groups.items() just above -- true because
            # dicts preserve insertion order and the list comprehension
            # iterates that same mapping once, in order.
            for mat, result in zip(material_groups.keys(), results):
                if result.material != mat:
                    st.info(L("matched_existing", typed=mat, actual=result.material))
                st.success(L("ingested_success", n=len(result.specimens), material=result.material, run_dir=result.run_dir))
                st.code(result.summary())
                for name, why in result.skipped:
                    st.warning(L("skipped", name=name, why=why))
