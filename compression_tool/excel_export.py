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
from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd
import xlsxwriter

from . import diagnostics
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
    "first_cycle_peak": {"en": "First cycle peak stress (MPa)", "de": "Spitzenspannung erster Zyklus (MPa)"},
    "last_cycle_peak": {"en": "Last cycle peak stress (MPa)", "de": "Spitzenspannung letzter Zyklus (MPa)"},
    "cycles_with_hold": {"en": "Cycles with a detected hold", "de": "Zyklen mit erkanntem Halten"},
    "cycles_of": {"en": "{held} of {total}", "de": "{held} von {total}"},
    "total_permdef_mm": {"en": "Total permanent deformation (mm)", "de": "Gesamte bleibende Verformung (mm)"},
    "total_permdef_pct": {"en": "Total permanent deformation (%)", "de": "Gesamte bleibende Verformung (%)"},
    "mean_hyst_multistage": {"en": "Mean hysteresis loss across cycles (-)", "de": "Mittlerer Hystereseverlust über die Zyklen (-)"},
    "mean_hyst_multistage_note": {
        "en": "  └ not a single physical value: multi-stage cycles span "
              "different stress levels; compare per-cycle instead",
        "de": "  └ kein einzelner physikalischer Wert: die Zyklen einer "
              "mehrstufigen Prüfung umfassen unterschiedliche "
              "Spannungsniveaus; stattdessen je Zyklus vergleichen"},
    "mean_hyst": {"en": "Mean hysteresis loss (-)", "de": "Mittlerer Hystereseverlust (-)"},
    "total_hold_disp": {"en": "Total hold displacement (mm)", "de": "Gesamter Haltewegzuwachs (mm)"},
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
    "derived_reference_levels": {"en": "Derived reference levels", "de": "Abgeleitete Referenzwerte"},
    "reference_stress_for": {"en": "{label}: reference stress (MPa)", "de": "{label}: Referenzspannung (MPa)"},
    "statistics_sheet": {"en": "Statistics", "de": "Statistik"},
    "cross_specimen_stats": {"en": "Cross-specimen statistics", "de": "Probenübergreifende Statistik"},
    "n_specimens": {"en": "n = {n} specimens", "de": "n = {n} Proben"},
    "col_cycle": {"en": "Cycle", "de": "Zyklus"},
    "col_mean": {"en": "Mean", "de": "Mittelwert"},
    "col_std": {"en": "Std dev", "de": "Standardabw."},
    "col_cov": {"en": "CoV (%)", "de": "VK (%)"},
}


def _t(key: str, lang: str, **kw) -> str:
    s = _EXCEL_T[key][lang]
    return s.format(**kw) if kw else s


def _col_label(col: Column, lang: str) -> str:
    return column_label_de(col) if lang == "de" else col.label


def _col_header(col: Column, lang: str) -> str:
    return column_header_de(col) if lang == "de" else col.header


def _col_desc(col: Column, lang: str) -> str:
    return column_description_de(col) if lang == "de" else col.description


def _field_label(field: Field, lang: str) -> str:
    return specimen_label_de(field) if lang == "de" else field.label


def _field_desc(field: Field, lang: str) -> str:
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
        if field.key in merged:
            value = merged[field.key]
            if field.key == "multi_stage":
                value = bool(value)
            label = _field_label(field, lang)
            header = f"{label} ({field.unit})" if field.unit else label
            pairs.append((header, value))

    def last_present(key: str) -> Optional[float]:
        vals = [c.get(key) for c in cycles if c.get(key) is not None]
        return vals[-1] if vals else None

    def mean_present(key: str) -> Optional[float]:
        vals = [c.get(key) for c in cycles if c.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    def total_present(key: str) -> Optional[float]:
        vals = [c.get(key) for c in cycles if c.get(key) is not None]
        return sum(vals) if vals else None

    peaks = [c.get("PeakStress_MPa") for c in cycles if c.get("PeakStress_MPa") is not None]
    holds = sum(1 for c in cycles if c.get("HoldDetected"))

    pairs.append(("", ""))
    pairs.append((_t("test_summary", lang), ""))
    if peaks:
        pairs.append((_t("first_cycle_peak", lang), peaks[0]))
        pairs.append((_t("last_cycle_peak", lang), peaks[-1]))
    pairs.append((_t("cycles_with_hold", lang), _t("cycles_of", lang, held=holds, total=len(cycles))))
    pairs.append((_t("total_permdef_mm", lang), last_present("PermDef_cumulative_mm")))
    if analysis.get("has_strain"):
        pairs.append((_t("total_permdef_pct", lang), last_present("PermDef_cumulative_pct")))

    # Multi-stage cycles span different stress levels, and hysteresis loss is
    # not flat across a stress range (it climbed from 0.55 to 0.93 across the
    # nine T050E1 stages) -- so a mean across them is not one physical value
    # the way it would be for a constant-amplitude test. Scope the label
    # rather than let it read as a single material constant.
    if bool(analysis.get("multi_stage")):
        pairs.append((
            _t("mean_hyst_multistage", lang),
            mean_present("HysteresisLoss_rel"),
        ))
        pairs.append((_t("mean_hyst_multistage_note", lang), ""))
    else:
        pairs.append((_t("mean_hyst", lang), mean_present("HysteresisLoss_rel")))
    pairs.append((_t("total_hold_disp", lang), total_present("Creep_during_hold_mm")))

    basis = analysis.get("strain_basis") or {}
    if analysis.get("has_strain"):
        pairs.append(("", ""))
        pairs.append((_t("strain_basis", lang), ""))
        pairs.append((_t("gauge_length_h0", lang), basis.get("h0_mm")))
        pairs.append((_t("measured_by_channel", lang), basis.get("displacement_channel")))
        pairs.append((_t("gauge_length_confirmed", lang), bool(basis.get("gauge_length_confirmed"))))
        pairs.append((
            _t("strain_status", lang),
            _t("validated", lang) if basis.get("strain_valid") else _t("provisional", lang),
        ))
    # Warnings are NOT appended here: with more than one specimen they would
    # repeat the same paragraph once per column. _write_summary() writes them
    # once, below the per-specimen fields, via diagnostics.distinct().
    return pairs


# ----------------------------------------------------------------------------
# Workbook
# ----------------------------------------------------------------------------


def write_workbook(payloads: Sequence[dict], path: str | Path, lang: str = "en") -> Path:
    """Write one workbook covering one or more specimens.

    Sheets: Summary, Cycles, Statistics (only with >1 specimen), Data
    dictionary, Config.

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
    _write_statistics(book, f, numfmt, payloads, lang)
    _write_dictionary(book, f, payloads, lang)
    _write_config(book, f, payloads, lang)

    book.close()
    os.replace(tmp, path)
    return path


def _write_summary(book, f, payloads: Sequence[dict], lang: str = "en") -> None:
    """Fields down the page, specimens across it: readable for one specimen,
    directly comparable for the two that a series export produces."""
    sheet = book.add_worksheet(_t("summary_sheet", lang))
    sheet.write(0, 0, _t("summary_title", lang), f["title"])

    columns = [summary_pairs(p, lang) for p in payloads]
    labels = [p.get("specimen", {}).get("label", _t("specimen_n", lang, n=i + 1))
              for i, p in enumerate(payloads)]

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
    cols = user_facing_cycle_columns(has_strain)

    col0 = 1 if multi else 0
    if multi:
        sheet.write(0, 0, _t("specimen", lang), f["head"])

    for i, col in enumerate(cols):
        sheet.write(0, col0 + i, _col_header(col, lang), f["head"])

    r = 1
    widths: dict[int, list[Any]] = {i: [] for i in range(len(cols))}
    for payload in payloads:
        label = payload.get("specimen", {}).get("label", "")
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
        longest = max((len(p.get("specimen", {}).get("label", "")) for p in payloads), default=10)
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
    for col in user_facing_cycle_columns(has_strain):
        sheet.write(r, 0, _col_label(col, lang), f["text"])
        sheet.write(r, 1, col.unit or "-", f["unit"])
        sheet.write(r, 2, _col_desc(col, lang), f["wrap"])
        r += 1

    r += 1
    sheet.write(r, 0, _t("summary_fields", lang), f["section"])
    r += 1
    for field in SPECIMEN_FIELDS:
        sheet.write(r, 0, _field_label(field, lang), f["text"])
        sheet.write(r, 1, field.unit or "-", f["unit"])
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
        sheet.write(r, 0, key, f["key"])
        if value is None:
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

    r += 2
    sheet.write(r, 0, _t("derived_reference_levels", lang), f["section"])
    r += 1
    for payload in payloads:
        analysis = payload.get("analysis", {})
        label = payload.get("specimen", {}).get("label", "")
        sheet.write(r, 0, _t("reference_stress_for", lang, label=label), f["key"])
        _write_value(sheet, r, 1, analysis.get("residual_stress_mpa"), f["text"], f["num"])
        r += 1

    sheet.set_column(0, 0, 44)
    sheet.set_column(1, 1, 28)


# ----------------------------------------------------------------------------
# Cross-specimen statistics
# ----------------------------------------------------------------------------

# Mirrors the source export's own "Statistik" sheet (x / s / n[%] per
# quantity), extended across every cycle instead of a single Fmax reading.
STATS_COLUMNS: tuple[str, ...] = (
    "PeakStress_MPa",
    "MaxDisp_mm",
    "PeakDisp_mm",
    "StressAtMaxDisp_MPa",
    "Stiffness_common_MPa_per_mm",
    "HysteresisLoss_rel",
    "PermDef_cumulative_mm",
    "Creep_during_hold_mm",
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


def _write_statistics(book, f, numfmt, payloads: Sequence[dict], lang: str = "en") -> None:
    """Mean / std / CoV per cycle across specimens -- the same shape as the
    source export's own Statistik sheet (x / s / n[%]), extended to every
    cycle rather than a single Fmax reading."""
    stats = cross_specimen_stats(payloads, lang)
    if not stats:
        return
    n_specimens = len({p.get("specimen", {}).get("label", "") for p in payloads})

    sheet = book.add_worksheet(_t("statistics_sheet", lang))
    sheet.write(0, 0, _t("cross_specimen_stats", lang), f["title"])
    sheet.write(1, 0, _t("n_specimens", lang, n=n_specimens), f["text"])

    r = 3
    for entry in stats:
        header = entry["label"] + (f" ({entry['unit']})" if entry["unit"] else "")
        sheet.write(r, 0, header, f["section"])
        r += 1
        for i, h in enumerate((_t("col_cycle", lang), _t("col_mean", lang), _t("col_std", lang), _t("col_cov", lang))):
            sheet.write(r, i, h, f["head"])
        r += 1
        for row in entry["rows"]:
            sheet.write_number(r, 0, row["cycle"], numfmt("0"))
            sheet.write_number(r, 1, row["mean"], numfmt("0.0000"))
            sheet.write_number(r, 2, row["std"], numfmt("0.0000"))
            if row["cov_pct"] is None:
                sheet.write_blank(r, 3, None)
            else:
                sheet.write_number(r, 3, row["cov_pct"], numfmt("0.00"))
            r += 1
        r += 1

    sheet.set_column(0, 0, 34)
    sheet.set_column(1, 3, 16)
    sheet.freeze_panes(3, 0)


# ----------------------------------------------------------------------------
# CSV -- the same flat table, for scripting
# ----------------------------------------------------------------------------


def cycles_dataframe(payloads: Sequence[dict], *, with_specimen: bool = False, lang: str = "en"):
    """Per-cycle table with the workbook's headers, as a DataFrame."""
    import pandas as pd

    has_strain = any(p.get("analysis", {}).get("has_strain") for p in payloads)
    cols = user_facing_cycle_columns(has_strain)

    rows = []
    for payload in payloads:
        label = payload.get("specimen", {}).get("label", "")
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
]
