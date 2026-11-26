"""
excel_export.py
===============
Workbook export: a flat per-cycle table with real headers and units, a summary
sheet, and a data dictionary.

The data dictionary is not decoration. The per-cycle table carries two
stiffness columns that look interchangeable and are not, and a permanent
deformation column that is not compression set; anyone reading the workbook
without the surrounding conversation needs those distinctions in the file
itself. Every description comes from schema.py, so the workbook cannot drift
out of step with the engine.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd
import xlsxwriter

from . import diagnostics
from .naming import short_names
from .timefmt import local_time
from .schema import (
    CYCLE_BY_KEY,
    HOLD_DISP_RATE,
    UNLOAD_YIELD,
    SPECIMEN_FIELDS,
    STIFFNESS_QUALITY,
    Column,
    Field,
    column_description_de,
    column_header_de,
    column_label_de,
    hold_disp_per_1000_samples,
    specimen_description_de,
    specimen_label_de,
    stiffness_quality,
    unload_yield_frac,
    user_facing_cycle_columns,
)

MAX_COL_WIDTH = 46
MIN_COL_WIDTH = 9


# ----------------------------------------------------------------------------
# Language
# ----------------------------------------------------------------------------
# German is a SEPARATE workbook (material_export.py writes both
# <material>.xlsx and <material>_de.xlsx from the same payloads), not a
# runtime toggle inside one file -- Excel has no script runtime to switch
# labels live the way results_dashboard.html's own EN/DE buttons do, so two
# static files is the whole answer here. `lang` threads through every
# function that touches a header/label/section string; the underlying
# numbers are identical in both files, only the text around them changes.
_EXCEL_T = {
    "summary_sheet": {"en": "Summary", "de": "Zusammenfassung"},
    "summary_title": {"en": "Compression test summary", "de": "Zusammenfassung des Druckversuchs"},
    "field": {"en": "Field", "de": "Feld"},
    "specimen_n": {"en": "Specimen {n}", "de": "Probe {n}"},
    "test_summary": {"en": "Test summary", "de": "Zusammenfassung der Prüfung"},
    "cycles_with_hold": {"en": "Cycles with a detected hold", "de": "Zyklen mit erkanntem Halten"},
    "cycles_of": {"en": "{held} of {total}", "de": "{held} von {total}"},
    # "Total permanent deformation" -> "Total deformation" and "Total hold
    # displacement" -> "Total creep at peak load", matching the renamed
    # fields (PermDef_cumulative_mm/Creep_during_hold_mm) everywhere else.
    # First/last cycle peak stress and mean hysteresis loss rows, and their
    # translation keys, were removed along with those two fields entirely.
    "total_permdef_mm": {"en": "Total deformation (mm)", "de": "Gesamtverformung (mm)"},
    "total_permdef_pct": {"en": "Total deformation (%)", "de": "Gesamtverformung (%)"},
    "total_hold_disp": {"en": "Total creep at peak load (mm)", "de": "Gesamter Kriechweg am Spitzenwert (mm)"},
    "strain_basis": {"en": "Strain basis", "de": "Dehnungsbasis"},
    "gauge_length_h0": {"en": "Gauge length h0 (mm)", "de": "Messlänge h0 (mm)"},
    "measured_by_channel": {"en": "Measured by channel", "de": "Gemessen über Kanal"},
    "gauge_length_confirmed": {"en": "Gauge length confirmed", "de": "Messlänge bestätigt"},
    "strain_status": {"en": "Strain / modulus status", "de": "Status Dehnung / Modul"},
    "validated": {"en": "validated", "de": "bestätigt"},
    "provisional": {"en": "PROVISIONAL - gauge length unconfirmed", "de": "VORLÄUFIG - Messlänge nicht bestätigt"},
    "read_before_quoting": {"en": "Read this before quoting the numbers", "de": "Vor dem Zitieren der Zahlen lesen"},
    "specimen": {"en": "Specimen", "de": "Probe"},
    "cycles_sheet": {"en": "Cycles", "de": "Zyklen"},
    "dictionary_sheet": {"en": "Data dictionary", "de": "Datenwörterbuch"},
    "dictionary_title": {"en": "What each column means", "de": "Was jede Spalte bedeutet"},
    "column": {"en": "Column", "de": "Spalte"},
    "unit": {"en": "Unit", "de": "Einheit"},
    "definition": {"en": "Definition", "de": "Definition"},
    "per_cycle_columns": {"en": "Per-cycle columns", "de": "Spalten je Zyklus"},
    "summary_fields": {"en": "Summary fields", "de": "Felder der Zusammenfassung"},
    "config_sheet": {"en": "Config", "de": "Konfiguration"},
    "config_title": {"en": "Analysis settings used", "de": "Verwendete Analyseeinstellungen"},
    "setting": {"en": "Setting", "de": "Einstellung"},
    "value": {"en": "Value", "de": "Wert"},
    "auto": {"en": "auto", "de": "automatisch"},
    "differing_settings_warning": {
        "en": "Warning: specimens in this workbook were analysed with different settings.",
        "de": "Achtung: Proben in dieser Arbeitsmappe wurden mit unterschiedlichen Einstellungen analysiert."},
    "trace_sheet": {"en": "Traceability", "de": "Rückverfolgbarkeit"},
    "trace_title": {"en": "Where these numbers come from", "de": "Woher diese Zahlen stammen"},
    "trace_fields": {"en": "Traceability fields", "de": "Felder der Rückverfolgbarkeit"},
    "yes": {"en": "yes", "de": "ja"},
    "no": {"en": "no", "de": "nein"},
}


# ----------------------------------------------------------------------------
# Plain-language texts for the workbook
# ----------------------------------------------------------------------------
# The workbook is read by people who never see the app's code, so its column
# and field descriptions are written here in plain words rather than taken
# from schema.py's developer descriptions. Labels still come from schema.py
# unless overridden here.

STRESS_STEP_MPA = 5  # same rounding as the Results and Compare pages

STRESS_STEP = Column(
    "_StressStep", "REAL", "Stress step", "MPa", "", fmt="0")

_COL_TEXT = {
    "Cycle": {
        "en": "Number of the load step, counted after the seating cycle.",
        "de": "Nummer der Laststufe, gezählt nach dem Setzzyklus."},
    "_StressStep": {
        "label_de": "Spannungsstufe",
        "en": "Peak stress of the cycle, rounded to 5 MPa. The same grouping the "
              "app uses on the Results and Compare pages.",
        "de": "Spitzenspannung des Zyklus, auf 5 MPa gerundet. Dieselbe Einteilung "
              "wie auf den Seiten Ergebnisse und Vergleich."},
    "Stiffness_common_MPa_per_mm": {
        "en": "Slope of the loading curve over the same stress range in every "
              "cycle. Use this value to compare cycles, specimens and materials.",
        "de": "Steigung der Belastungskurve über denselben Spannungsbereich in "
              "jedem Zyklus. Diesen Wert für Vergleiche zwischen Zyklen, Proben "
              "und Materialien verwenden."},
    "Stiffness_relative_MPa_per_mm": {
        "en": "Slope of the loading curve between 25 % and 75 % of this cycle's "
              "own peak stress. The range moves up with every step, so "
              "do not compare different cycles on this value.",
        "de": "Steigung der Belastungskurve zwischen 25 % und 75 % der eigenen "
              "Spitzenspannung dieses Zyklus. Der Bereich wandert mit "
              "jeder Stufe nach oben, daher verschiedene Zyklen nicht über diesen "
              "Wert vergleichen."},
    "MaxDisp_mm": {
        "en": "Largest displacement in the cycle, including the creep while the "
              "load was held.",
        "de": "Größter Weg im Zyklus, einschließlich des Kriechens während des "
              "Haltens."},
    "PermDef_cumulative_mm": {
        "en": "Permanent deformation (what did not spring back), added up over "
              "all cycles so far. This is not compression set in the sense of "
              "ISO 815 or ASTM D395.",
        "de": "Bleibende Verformung (was nicht zurückfedert), über alle bisherigen "
              "Zyklen aufsummiert. Das ist kein Druckverformungsrest im Sinne von "
              "ISO 815 oder ASTM D395."},
    "PermDef_cumulative_pct": {
        "en": "The same total deformation as a share of the specimen height h0.",
        "de": "Dieselbe Gesamtverformung als Anteil der Probenhöhe h0."},
    "Creep_during_hold_mm": {
        "en": "How far the specimen kept sinking while the load was held at the "
              "top. A total, not a rate. Empty when the cycle has no hold.",
        "de": "Wie weit die Probe beim Halten der Last oben noch nachgegeben hat. "
              "Eine Summe, keine Rate. Leer, wenn der Zyklus kein Halten hat."},
    "Energy_dissipated_MPa_mm": {
        "en": "Area of the load-unload loop: the energy lost in the cycle per unit "
              "area. Divide by h0 for energy per unit volume.",
        "de": "Fläche der Be- und Entlastungsschleife: die im Zyklus verlorene "
              "Energie je Flächeneinheit. Durch h0 teilen für Energie je "
              "Volumeneinheit."},
}

# Summary sheet: what a reader needs. Everything else about the source goes
# to the Traceability sheet.
SUMMARY_KEYS = ("material", "source_filename", "displacement_channel", "h0_mm",
                "d0_mm", "temperature_c", "n_cycles", "global_peak_mpa",
                "residual_stress_mpa")
TRACE_KEYS = ("label", "specimen_id", "source_format", "source_sha256", "raw_input_path",
              "n_points", "multi_stage", "created_utc")

_FIELD_TEXT = {
    "material": {"en": "Material name given when the test was saved.",
                 "de": "Beim Speichern angegebener Materialname."},
    "source_filename": {"en": "Name of the testXpert export file.",
                        "de": "Name der testXpert-Exportdatei."},
    "displacement_channel": {
        "en": "Channel the displacement was read from. The extensometer is "
              "preferred; the machine travel also contains the machine's own "
              "deflection.",
        "de": "Kanal, aus dem der Weg gelesen wurde. Der Feindehnungsaufnehmer "
              "wird bevorzugt; der Maschinenweg enthält auch die Nachgiebigkeit "
              "der Maschine."},
    "h0_mm": {"en": "Specimen height, read from the export.",
              "de": "Probenhöhe, aus dem Export gelesen."},
    "d0_mm": {"en": "Specimen diameter, read from the export.",
              "de": "Probendurchmesser, aus dem Export gelesen."},
    "temperature_c": {"en": "Test temperature, read from the export.",
                      "de": "Prüftemperatur, aus dem Export gelesen."},
    "n_cycles": {"en": "Number of cycles evaluated, without the seating cycle.",
                 "de": "Anzahl ausgewerteter Zyklen, ohne den Setzzyklus."},
    "global_peak_mpa": {"en": "Highest stress reached in the test.",
                        "de": "Höchste in der Prüfung erreichte Spannung."},
    "residual_stress_mpa": {
        "en": "Low stress level (1 % of the highest stress by default) at which "
              "the permanent deformation is read.",
        "de": "Niedrige Spannung (standardmäßig 1 % der höchsten Spannung), bei "
              "der die bleibende Verformung abgelesen wird."},
    "label": {"en": "Full specimen name as it is in the export. The other sheets "
                    "use a short form of it.",
              "de": "Vollständiger Probenname wie im Export. Die anderen Blätter "
                    "verwenden eine Kurzform."},
    "specimen_id": {"en": "Fixed ID of the specimen in the library.",
                    "de": "Feste Kennung der Probe in der Bibliothek."},
    "source_format": {
        "en": "'series' = several specimens in one workbook, 'single' = one "
              "specimen per sheet.",
        "de": "'series' = mehrere Proben in einer Arbeitsmappe, 'single' = eine "
              "Probe je Blatt."},
    "source_sha256": {
        "en": "Check number of the original file's content. Two identical files "
              "have the same number.",
        "de": "Prüfnummer des Inhalts der Originaldatei. Zwei gleiche Dateien "
              "haben dieselbe Nummer."},
    "raw_input_path": {
        "en": "Where the unchanged copy of the export is kept in the library.",
        "de": "Wo die unveränderte Kopie des Exports in der Bibliothek liegt."},
    "n_points": {"en": "Number of measured points used.",
                 "de": "Anzahl verwendeter Messpunkte."},
    "multi_stage": {"en": "Yes when the cycles have different peak stresses (load steps).",
                    "de": "Ja, wenn die Zyklen unterschiedliche Spitzenspannungen haben (Laststufen)."},
    "created_utc": {"en": "When the analysis was run (local time of the computer that saved it).",
                    "de": "Zeitpunkt der Auswertung (Ortszeit des speichernden Rechners)."},
}

_SUMMARY_TOTAL_TEXT = {
    "total_permdef_mm": {
        "en": "Total deformation after the last cycle.",
        "de": "Gesamtverformung nach dem letzten Zyklus."},
    "total_permdef_pct": {
        "en": "The same, as a share of h0.",
        "de": "Dasselbe als Anteil von h0."},
    "total_hold_disp": {
        "en": "Creep at peak load, added up over all cycles.",
        "de": "Kriechweg am Spitzenwert, über alle Zyklen aufsummiert."},
}

_CONFIG_LABELS = {
    "unload_frac": ("Unload sensitivity", "Entlastungsempfindlichkeit"),
    "major_cycle_frac": ("Minimum cycle size", "Mindestzyklusgröße"),
    "min_cycle_points": ("Minimum points per cycle", "Mindestpunkte je Zyklus"),
    "hold_tol_frac": ("Hold detection tolerance", "Toleranz für Halteerkennung"),
    "hold_min_points": ("Minimum points in a hold", "Mindestpunkte beim Halten"),
    "detect_holds": ("Detect holds", "Halten erkennen"),
    "stiff_lo_frac": ("Stiffness window start (share of peak)",
                      "Steifigkeitsfenster Anfang (Anteil der Spitze)"),
    "stiff_hi_frac": ("Stiffness window end (share of peak)",
                      "Steifigkeitsfenster Ende (Anteil der Spitze)"),
    "residual_stress_mpa": ("Reference stress (MPa)", "Referenzspannung (MPa)"),
    "residual_stress_frac": ("Reference stress (share of highest stress)",
                             "Referenzspannung (Anteil der höchsten Spannung)"),
    "h0_mm": ("Specimen height h0 (mm)", "Probenhöhe h0 (mm)"),
}


def excel_cycle_columns(has_strain: bool) -> list[Column]:
    """Cycles-sheet columns: Cycle, the stress step, then the six headline
    properties, with total deformation also given in % when h0 is known."""
    out: list[Column] = []
    for col in user_facing_cycle_columns(has_strain):
        out.append(col)
        if col.key == "Cycle":
            out.append(STRESS_STEP)
        if col.key == "PermDef_cumulative_mm" and has_strain:
            out.append(replace(CYCLE_BY_KEY["PermDef_cumulative_pct"], internal=False))
    return out


def _short_labels(payloads: Sequence[dict], lang: str) -> list[str]:
    """Short specimen names for column headers and the Specimen column."""
    return short_names(p.get("specimen", {}).get("label") or _t("specimen_n", lang, n=i + 1)
                       for i, p in enumerate(payloads))


def _stress_step(peak: Any) -> Optional[float]:
    try:
        p = float(peak)
    except (TypeError, ValueError):
        return None
    if p != p:
        return None
    return float(round(p / STRESS_STEP_MPA) * STRESS_STEP_MPA)


def _t(key: str, lang: str, **kw) -> str:
    s = _EXCEL_T[key][lang]
    return s.format(**kw) if kw else s


def _col_label(col: Column, lang: str) -> str:
    if lang == "de":
        return _COL_TEXT.get(col.key, {}).get("label_de") or column_label_de(col)
    return col.label


def _col_header(col: Column, lang: str) -> str:
    if lang == "de":
        if "label_de" in _COL_TEXT.get(col.key, {}):
            return f"{_col_label(col, lang)} ({col.unit})" if col.unit else _col_label(col, lang)
        return column_header_de(col)
    return col.header


def _col_desc(col: Column, lang: str) -> str:
    text = _COL_TEXT.get(col.key)
    if text:
        return text[lang]
    return column_description_de(col) if lang == "de" else col.description


def _field_label(field: Field, lang: str) -> str:
    return specimen_label_de(field) if lang == "de" else field.label


def _field_desc(field: Field, lang: str) -> str:
    text = _FIELD_TEXT.get(field.key)
    if text:
        return text[lang]
    return specimen_description_de(field) if lang == "de" else field.description


def _warning_message(w: dict, lang: str) -> str:
    if lang == "de":
        return w.get("message_de") or w["message"]
    return w["message"]


# ----------------------------------------------------------------------------
# Cell helpers
# ----------------------------------------------------------------------------


def _cell(value: Any) -> Any:
    """Booleans read better as words than as 1/0 in a printed table."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return value


def _width_for(label: str, values: Sequence[Any]) -> float:
    longest = max((len(str(_cell(v))) for v in values if v is not None), default=0)
    # Headers wrap over two lines, so half the header length is enough.
    return max(MIN_COL_WIDTH, min(MAX_COL_WIDTH, max(longest + 2, len(label) / 2 + 4)))


def _write_value(sheet, row: int, col: int, value: Any, text_fmt, num_fmt) -> None:
    """Write with the type the value actually is.

    Strings are written as strings explicitly: a specimen id or content hash
    that happens to be all digits would otherwise be stored as a number, which
    both loses precision and displays as scientific notation.
    """
    if value is None or value == "":
        sheet.write_blank(row, col, None, text_fmt)
    elif isinstance(value, bool):
        sheet.write_string(row, col, "yes" if value else "no", text_fmt)
    elif isinstance(value, (int, float)):
        sheet.write_number(row, col, float(value), num_fmt)
    else:
        sheet.write_string(row, col, str(value), text_fmt)


def row_values(row: dict, cols: Sequence[Column]) -> list[Any]:
    out = []
    for col in cols:
        if col.key == STIFFNESS_QUALITY.key:
            out.append(stiffness_quality(row.get("Stiffness_common_n"),
                                         row.get("Stiffness_common_r2")))
        elif col.key == UNLOAD_YIELD.key:
            out.append(unload_yield_frac(row.get("StressAtMaxDisp_MPa"),
                                         row.get("PeakStress_MPa")))
        elif col.key == STRESS_STEP.key:
            out.append(_stress_step(row.get("PeakStress_MPa")))
        elif col.key == HOLD_DISP_RATE.key:
            out.append(hold_disp_per_1000_samples(row.get("Creep_during_hold_mm"),
                                                  row.get("HoldPoints")))
        else:
            out.append(row.get(col.key))
    return out


# ----------------------------------------------------------------------------
# Summary values
# ----------------------------------------------------------------------------


def summary_pairs(payload: dict, lang: str = "en") -> list[tuple[str, Any]]:
    """Specimen identity and provenance, plus the few whole-test aggregates
    that answer 'what happened here' without opening the cycle table."""
    spec, analysis = payload.get("specimen", {}), payload.get("analysis", {})
    cycles = payload.get("cycles", [])

    merged: dict[str, Any] = {**spec, **analysis, "created_utc": payload.get("created_utc")}
    pairs: list[tuple[str, Any]] = []
    for field in SPECIMEN_FIELDS:
        if field.key in SUMMARY_KEYS and field.key in merged:
            value = merged[field.key]
            if field.key == "multi_stage":
                value = bool(value)
            label = _field_label(field, lang)
            header = f"{label} ({field.unit})" if field.unit else label
            pairs.append((header, value))

    def last_present(key: str) -> Optional[float]:
        vals = [c.get(key) for c in cycles if c.get(key) is not None]
        return vals[-1] if vals else None

    def total_present(key: str) -> Optional[float]:
        vals = [c.get(key) for c in cycles if c.get(key) is not None]
        return sum(vals) if vals else None

    pairs.append(("", ""))
    pairs.append((_t("test_summary", lang), ""))
    pairs.append((_t("total_permdef_mm", lang), last_present("PermDef_cumulative_mm")))
    if analysis.get("has_strain"):
        pairs.append((_t("total_permdef_pct", lang), last_present("PermDef_cumulative_pct")))
    # First/last cycle peak stress and mean hysteresis loss rows were removed
    # along with Peak stress and Hysteresis loss as user-facing fields
    # (fields that are no longer shown) -- both derived straight from those two columns
    # and had no purpose left once the fields themselves were gone.
    pairs.append((_t("total_hold_disp", lang), total_present("Creep_during_hold_mm")))
    # Warnings are NOT appended here: with more than one specimen they would
    # repeat the same paragraph once per column. _write_summary() writes them
    # once, below the per-specimen fields, via diagnostics.distinct().
    return pairs


def traceability_pairs(payload: dict, lang: str = "en") -> list[tuple[str, Any]]:
    """Fields that tie a result to its source file and analysis, kept off the
    Summary sheet so the reader sees the results first."""
    spec, analysis = payload.get("specimen", {}), payload.get("analysis", {})
    merged: dict[str, Any] = {**spec, **analysis, "created_utc": payload.get("created_utc")}
    pairs: list[tuple[str, Any]] = []
    for field in SPECIMEN_FIELDS:
        if field.key in TRACE_KEYS and field.key in merged:
            value = merged[field.key]
            if field.key == "multi_stage":
                value = _t("yes" if value else "no", lang)
            label = _field_label(field, lang)
            if field.key == "created_utc":
                pairs.append((label, local_time(value)))
                continue
            pairs.append((f"{label} ({field.unit})" if field.unit else label, value))
    basis = analysis.get("strain_basis") or {}
    if analysis.get("has_strain"):
        pairs.append((_t("gauge_length_confirmed", lang),
                      _t("yes" if basis.get("gauge_length_confirmed") else "no", lang)))
        pairs.append((
            _t("strain_status", lang),
            _t("validated", lang) if basis.get("strain_valid") else _t("provisional", lang),
        ))
    return pairs


# ----------------------------------------------------------------------------
# Workbook
# ----------------------------------------------------------------------------


def write_workbook(payloads: Sequence[dict], path: str | Path, lang: str = "en") -> Path:
    """Write one workbook covering one or more specimens.

    Sheets: Summary, Cycles, Data dictionary, Config, Traceability. (A "Statistics"
    cross-specimen sheet used to sit between Cycles and Data dictionary --
    removed entirely, since it was covering the same headline
    metrics per-cycle bar charts already show, one abstraction layer up.
    cross_specimen_stats() itself stays -- html_report.py's own "Cross-
    specimen statistics" section still uses it.)

    `lang`: "en" or "de". Every header/label/description in the workbook is
    picked for that language (schema.py's *_de lookups and this module's own
    _EXCEL_T); the underlying numbers never change. material_export.py calls
    this twice, once per language, into two separate files -- Excel has no
    runtime to switch language inside one workbook the way the HTML
    dashboard's own buttons do.

    Written atomically (a `.partial` file, then `os.replace`): xlsxwriter
    writes progressively into the file as sheets are built, so a plain
    `Workbook(path)` leaves a reader able to open a truncated file for the
    whole time the workbook is being built. That window matters most for
    reports/<material>.xlsx, which every ingest of that material rewrites
    from scratch -- without this, two ingesters landing on the same material
    around the same time could each see the other's half-written workbook,
    not just an old-but-complete one.
    """
    if not payloads:
        raise ValueError("write_workbook needs at least one specimen payload")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    book = xlsxwriter.Workbook(str(tmp), {"nan_inf_to_errors": True})

    f = {
        "title": book.add_format({"bold": True, "font_size": 14}),
        "head": book.add_format({
            "bold": True, "text_wrap": True, "valign": "bottom",
            "bg_color": "#1F3864", "font_color": "#FFFFFF", "border": 1,
        }),
        "key": book.add_format({"bold": True, "valign": "top"}),
        "section": book.add_format({"bold": True, "font_color": "#1F3864", "top": 1}),
        "text": book.add_format({"valign": "top"}),
        "wrap": book.add_format({"text_wrap": True, "valign": "top"}),
        "unit": book.add_format({"italic": True, "font_color": "#555555", "valign": "top"}),
        "warn": book.add_format({"bg_color": "#FFF2CC", "font_color": "#7F6000"}),
        "bad": book.add_format({"bg_color": "#FCE4E4", "font_color": "#9C0006"}),
        # Summary and config values span many magnitudes -- a peak stress in
        # the hundreds next to a permanent set of a few micrometres -- so they
        # get a format wide enough for both that drops trailing zeros.
        "num": book.add_format({"num_format": "#,##0.######", "valign": "top"}),
    }
    num_formats: dict[str, Any] = {}

    def numfmt(fmt: str):
        if fmt not in num_formats:
            num_formats[fmt] = book.add_format({"num_format": fmt})
        return num_formats[fmt]

    _write_summary(book, f, payloads, lang)
    _write_cycles(book, f, numfmt, payloads, lang)
    _write_dictionary(book, f, payloads, lang)
    _write_config(book, f, payloads, lang)
    _write_traceability(book, f, payloads, lang)

    book.close()
    os.replace(tmp, path)
    return path


def _write_summary(book, f, payloads: Sequence[dict], lang: str = "en") -> None:
    """Fields down the page, specimens across it: readable for one specimen,
    directly comparable for the two that a series export produces."""
    sheet = book.add_worksheet(_t("summary_sheet", lang))
    sheet.write(0, 0, _t("summary_title", lang), f["title"])

    columns = [summary_pairs(p, lang) for p in payloads]
    labels = _short_labels(payloads, lang)

    row0 = 2
    sheet.write(row0, 0, _t("field", lang), f["head"])
    for i, label in enumerate(labels):
        sheet.write(row0, 1 + i, label, f["head"])

    # All specimens share the field order; take the longest as the spine.
    spine = max(columns, key=len)
    r = row0
    for r, (key, _) in enumerate(spine, start=row0 + 1):
        style = f["section"] if (key and not _has_value(columns, r - row0 - 1)) else f["key"]
        sheet.write(r, 0, key, style if key else f["text"])
        for i, pairs in enumerate(columns):
            value = pairs[r - row0 - 1][1] if r - row0 - 1 < len(pairs) else None
            _write_value(sheet, r, 1 + i, value, f["text"], f["num"])

    # Warnings, written ONCE below every specimen column -- not per specimen,
    # the way the fields above are. Specimens under the same config typically
    # trip the same warnings; repeating the paragraph once per column would
    # just be the same text side by side.
    warnings = diagnostics.distinct(payloads)
    if warnings:
        r += 2
        sheet.write(r, 0, _t("read_before_quoting", lang), f["section"])
        last_col = max(1, len(labels))
        for w in warnings:
            r += 1
            style = f["bad"] if w["severity"] == "critical" else (
                f["warn"] if w["severity"] == "caution" else f["text"])
            sheet.write(r, 0, w["severity"].upper(), style)
            message = _warning_message(w, lang)
            if last_col > 1:
                sheet.merge_range(r, 1, r, last_col, message, f["wrap"])
            else:
                sheet.write(r, 1, message, f["wrap"])

    sheet.set_column(0, 0, 34)
    sheet.set_column(1, len(labels), 34)
    sheet.freeze_panes(row0 + 1, 1)


def _has_value(columns: Sequence[list[tuple[str, Any]]], idx: int) -> bool:
    for pairs in columns:
        if idx < len(pairs) and pairs[idx][1] not in ("", None):
            return True
    return False


def _write_cycles(book, f, numfmt, payloads: Sequence[dict], lang: str = "en") -> None:
    sheet = book.add_worksheet(_t("cycles_sheet", lang))
    multi = len(payloads) > 1
    has_strain = any(p.get("analysis", {}).get("has_strain") for p in payloads)
    cols = excel_cycle_columns(has_strain)

    col0 = 1 if multi else 0
    if multi:
        sheet.write(0, 0, _t("specimen", lang), f["head"])

    for i, col in enumerate(cols):
        sheet.write(0, col0 + i, _col_header(col, lang), f["head"])

    r = 1
    widths: dict[int, list[Any]] = {i: [] for i in range(len(cols))}
    shorts = _short_labels(payloads, lang)
    for payload, label in zip(payloads, shorts):
        for row in payload.get("cycles", []):
            if multi:
                sheet.write(r, 0, label, f["text"])
            for i, (col, value) in enumerate(zip(cols, row_values(row, cols))):
                widths[i].append(value)
                cell = _cell(value)
                if cell is None:
                    sheet.write_blank(r, col0 + i, None)
                elif isinstance(cell, str):
                    style = None
                    if col.key == STIFFNESS_QUALITY.key:
                        style = f["bad"] if cell == "none" else (
                            f["warn"] if cell != "ok" else None)
                    sheet.write_string(r, col0 + i, cell, style)
                else:
                    sheet.write_number(r, col0 + i, float(cell), numfmt(col.fmt))
            r += 1

    if multi:
        longest = max((len(s) for s in shorts), default=10)
        sheet.set_column(0, 0, max(MIN_COL_WIDTH, min(MAX_COL_WIDTH, longest + 2)))
    for i, col in enumerate(cols):
        sheet.set_column(col0 + i, col0 + i, _width_for(_col_header(col, lang), widths[i]))

    sheet.set_row(0, 46)
    sheet.freeze_panes(1, col0 + 1)
    if r > 1:
        sheet.autofilter(0, 0, r - 1, col0 + len(cols) - 1)


def _write_dictionary(book, f, payloads: Sequence[dict], lang: str = "en") -> None:
    sheet = book.add_worksheet(_t("dictionary_sheet", lang))
    has_strain = any(p.get("analysis", {}).get("has_strain") for p in payloads)

    sheet.write(0, 0, _t("dictionary_title", lang), f["title"])
    r = 2
    headers = (_t("column", lang), _t("unit", lang), _t("definition", lang))
    for i, header in enumerate(headers):
        sheet.write(r, i, header, f["head"])
    r += 1

    sheet.write(r, 0, _t("per_cycle_columns", lang), f["section"])
    r += 1
    for col in excel_cycle_columns(has_strain):
        sheet.write(r, 0, _col_label(col, lang), f["text"])
        sheet.write(r, 1, col.unit or "-", f["unit"])
        sheet.write(r, 2, _col_desc(col, lang), f["wrap"])
        r += 1

    r += 1
    sheet.write(r, 0, _t("summary_fields", lang), f["section"])
    r += 1
    for field in SPECIMEN_FIELDS:
        if field.key not in SUMMARY_KEYS:
            continue
        sheet.write(r, 0, _field_label(field, lang), f["text"])
        sheet.write(r, 1, field.unit or "-", f["unit"])
        sheet.write(r, 2, _field_desc(field, lang), f["wrap"])
        r += 1
    for key, unit in (("total_permdef_mm", "mm"), ("total_permdef_pct", "%"),
                      ("total_hold_disp", "mm")):
        sheet.write(r, 0, _t(key, lang).rsplit(" (", 1)[0], f["text"])
        sheet.write(r, 1, unit, f["unit"])
        sheet.write(r, 2, _SUMMARY_TOTAL_TEXT[key][lang], f["wrap"])
        r += 1

    r += 1
    sheet.write(r, 0, _t("trace_fields", lang), f["section"])
    r += 1
    for field in SPECIMEN_FIELDS:
        if field.key not in TRACE_KEYS:
            continue
        sheet.write(r, 0, _field_label(field, lang), f["text"])
        unit = "-" if field.key == "created_utc" else (field.unit or "-")
        sheet.write(r, 1, unit, f["unit"])
        sheet.write(r, 2, _field_desc(field, lang), f["wrap"])
        r += 1

    sheet.set_column(0, 0, 38)
    sheet.set_column(1, 1, 10)
    sheet.set_column(2, 2, 96)
    sheet.freeze_panes(3, 0)


def _write_config(book, f, payloads: Sequence[dict], lang: str = "en") -> None:
    """The settings behind the numbers, so a result can be reproduced or
    challenged without hunting for the script that produced it."""
    sheet = book.add_worksheet(_t("config_sheet", lang))
    sheet.write(0, 0, _t("config_title", lang), f["title"])
    sheet.write(2, 0, _t("setting", lang), f["head"])
    sheet.write(2, 1, _t("value", lang), f["head"])

    cfg = payloads[0].get("config", {})
    r = 3
    for key, value in cfg.items():
        labels = _CONFIG_LABELS.get(key)
        sheet.write(r, 0, (labels[1] if lang == "de" else labels[0]) if labels else key, f["key"])
        if isinstance(value, bool):
            sheet.write_string(r, 1, _t("yes" if value else "no", lang), f["text"])
        elif value is None:
            # A None knob is not missing data: it means the engine derives the
            # level from the test itself.
            sheet.write_string(r, 1, _t("auto", lang), f["text"])
        else:
            _write_value(sheet, r, 1, value, f["text"], f["num"])
        r += 1

    differing = [p for p in payloads[1:] if p.get("config") != cfg]
    if differing:
        r += 1
        sheet.write(r, 0, _t("differing_settings_warning", lang), f["section"])

    sheet.set_column(0, 0, 44)
    sheet.set_column(1, 1, 28)


def _write_traceability(book, f, payloads: Sequence[dict], lang: str = "en") -> None:
    """Source file, check number and analysis time per specimen."""
    sheet = book.add_worksheet(_t("trace_sheet", lang))
    sheet.write(0, 0, _t("trace_title", lang), f["title"])
    labels = _short_labels(payloads, lang)
    columns = [traceability_pairs(p, lang) for p in payloads]
    row0 = 2
    sheet.write(row0, 0, _t("field", lang), f["head"])
    for i, label in enumerate(labels):
        sheet.write(row0, 1 + i, label, f["head"])
    spine = max(columns, key=len)
    for j, (key, _) in enumerate(spine):
        sheet.write(row0 + 1 + j, 0, key, f["key"])
        for i, pairs in enumerate(columns):
            value = pairs[j][1] if j < len(pairs) else None
            _write_value(sheet, row0 + 1 + j, 1 + i, value, f["text"], f["num"])
    sheet.set_column(0, 0, 34)
    sheet.set_column(1, len(labels), 34)
    sheet.freeze_panes(row0 + 1, 1)


# ----------------------------------------------------------------------------
# Cross-specimen statistics
# ----------------------------------------------------------------------------

# Mirrors the source export's own "Statistik" sheet (x / s / n[%] per
# quantity), extended across every cycle instead of a single Fmax reading.
#
# Ordered to match schema.HEADLINE_CYCLE_BLOCKS (the same six-field
# sequence requested for the dashboard, Compare's picker, and the Cycles/
# CSV export) followed by the remaining, non-headline detail columns this
# sheet already carried. Peak stress and Hysteresis loss are gone --
# no longer shown as user-facing fields, same as everywhere else --
# and Stiffness (relative band) plus Energy dissipated were added: this
# sheet is meant to be a full "dashboard replica," and both were missing
# from it entirely even before this change.
STATS_COLUMNS: tuple[str, ...] = (
    "Stiffness_common_MPa_per_mm",
    "Stiffness_relative_MPa_per_mm",
    "MaxDisp_mm",
    "PermDef_cumulative_mm",
    "Creep_during_hold_mm",
    "Energy_dissipated_MPa_mm",
    "PeakDisp_mm",
    "StressAtMaxDisp_MPa",
)
STATS_COLUMNS_STRAIN: tuple[str, ...] = ("PermDef_cumulative_pct",)


def cross_specimen_stats(payloads: Sequence[dict], lang: str = "en") -> list[dict]:
    """Mean / std / coefficient of variation per cycle, across specimens.

    Only meaningful with more than one specimen -- with one there is nothing
    to compare, so this returns [] and callers skip the section, the same way
    a single-specimen run skips the combined workbook.
    """
    if len(payloads) < 2:
        return []
    has_strain = any(p.get("analysis", {}).get("has_strain") for p in payloads)
    keys = list(STATS_COLUMNS) + (list(STATS_COLUMNS_STRAIN) if has_strain else [])

    rows = [c for p in payloads for c in p.get("cycles", [])]
    if not rows:
        return []
    df = pd.DataFrame(rows)

    out: list[dict] = []
    for key in keys:
        if key not in df.columns:
            continue
        col = CYCLE_BY_KEY.get(key)
        entry: dict[str, Any] = {
            "key": key,
            "label": (_col_label(col, lang) if col else key),
            "unit": col.unit if col else "",
            "rows": [],
        }
        for cycle, values in df.groupby("Cycle")[key]:
            v = pd.to_numeric(values, errors="coerce").dropna()
            if v.empty:
                continue
            mean = float(v.mean())
            std = float(v.std(ddof=0)) if len(v) > 1 else 0.0
            cov_pct = (std / abs(mean) * 100.0) if mean else None
            entry["rows"].append({
                "cycle": int(cycle), "n": int(len(v)),
                "mean": mean, "std": std, "cov_pct": cov_pct,
            })
        if entry["rows"]:
            out.append(entry)
    return out


# ----------------------------------------------------------------------------
# CSV -- the same flat table, for scripting
# ----------------------------------------------------------------------------


def cycles_dataframe(payloads: Sequence[dict], *, with_specimen: bool = False, lang: str = "en"):
    """Per-cycle table with the workbook's headers, as a DataFrame."""
    import pandas as pd

    has_strain = any(p.get("analysis", {}).get("has_strain") for p in payloads)
    cols = excel_cycle_columns(has_strain)

    rows = []
    for payload, label in zip(payloads, _short_labels(payloads, lang)):
        for row in payload.get("cycles", []):
            record = {}
            if with_specimen:
                record[_t("specimen", lang)] = label
            for col, value in zip(cols, row_values(row, cols)):
                record[_col_header(col, lang)] = value
            rows.append(record)
    return pd.DataFrame(rows)


def write_csv(
    payloads: Sequence[dict], path: str | Path, *, with_specimen: bool = False, lang: str = "en",
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    cycles_dataframe(payloads, with_specimen=with_specimen, lang=lang).to_csv(tmp, index=False)
    os.replace(tmp, path)
    return path


__all__ = [
    "write_workbook",
    "write_csv",
    "cycles_dataframe",
    "cross_specimen_stats",
    "row_values",
    "summary_pairs",
    "traceability_pairs",
    "excel_cycle_columns",
]
