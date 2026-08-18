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

from pathlib import Path
from typing import Any, Optional, Sequence

import pandas as pd
import xlsxwriter

from . import diagnostics
from .schema import (
    CYCLE_BY_KEY,
    HOLD_DISP_RATE,
    SPECIMEN_FIELDS,
    STIFFNESS_QUALITY,
    Column,
    hold_disp_per_1000_samples,
    stiffness_quality,
    user_facing_cycle_columns,
)

MAX_COL_WIDTH = 46
MIN_COL_WIDTH = 9


# ----------------------------------------------------------------------------
# Cell helpers
# ----------------------------------------------------------------------------


def _cell(value: Any) -> Any:
    """Booleans read better as words than as 1/0 in a printed table."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return value


def _width_for(col: Column, values: Sequence[Any]) -> float:
    longest = max((len(str(_cell(v))) for v in values if v is not None), default=0)
    # Headers wrap over two lines, so half the header length is enough.
    return max(MIN_COL_WIDTH, min(MAX_COL_WIDTH, max(longest + 2, len(col.label) / 2 + 4)))


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
        elif col.key == HOLD_DISP_RATE.key:
            out.append(hold_disp_per_1000_samples(row.get("Creep_during_hold_mm"),
                                                  row.get("HoldPoints")))
        else:
            out.append(row.get(col.key))
    return out


# ----------------------------------------------------------------------------
# Summary values
# ----------------------------------------------------------------------------


def summary_pairs(payload: dict) -> list[tuple[str, Any]]:
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
            pairs.append((field.header, value))

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
    pairs.append(("Test summary", ""))
    if peaks:
        pairs.append(("First cycle peak stress (MPa)", peaks[0]))
        pairs.append(("Last cycle peak stress (MPa)", peaks[-1]))
    pairs.append(("Cycles with a detected hold", f"{holds} of {len(cycles)}"))
    pairs.append(("Total permanent deformation (mm)", last_present("PermDef_cumulative_mm")))
    if analysis.get("has_strain"):
        pairs.append(("Total permanent deformation (%)", last_present("PermDef_cumulative_pct")))

    # Multi-stage cycles span different stress levels, and hysteresis loss is
    # not flat across a stress range (it climbed from 0.55 to 0.93 across the
    # nine T050E1 stages) -- so a mean across them is not one physical value
    # the way it would be for a constant-amplitude test. Scope the label
    # rather than let it read as a single material constant.
    if bool(analysis.get("multi_stage")):
        pairs.append((
            "Mean hysteresis loss across cycles (-)",
            mean_present("HysteresisLoss_rel"),
        ))
        pairs.append((
            "  └ not a single physical value: multi-stage cycles span "
            "different stress levels; compare per-cycle instead",
            "",
        ))
    else:
        pairs.append(("Mean hysteresis loss (-)", mean_present("HysteresisLoss_rel")))
    pairs.append(("Total hold displacement (mm)", total_present("Creep_during_hold_mm")))

    basis = analysis.get("strain_basis") or {}
    if analysis.get("has_strain"):
        pairs.append(("", ""))
        pairs.append(("Strain basis", ""))
        pairs.append(("Gauge length h0 (mm)", basis.get("h0_mm")))
        pairs.append(("Measured by channel", basis.get("displacement_channel")))
        pairs.append(("Gauge length confirmed", bool(basis.get("gauge_length_confirmed"))))
        pairs.append((
            "Strain / modulus status",
            "validated" if basis.get("strain_valid") else "PROVISIONAL - gauge length unconfirmed",
        ))
    # Warnings are NOT appended here: with more than one specimen they would
    # repeat the same paragraph once per column. _write_summary() writes them
    # once, below the per-specimen fields, via diagnostics.distinct().
    return pairs


# ----------------------------------------------------------------------------
# Workbook
# ----------------------------------------------------------------------------


def write_workbook(payloads: Sequence[dict], path: str | Path) -> Path:
    """Write one workbook covering one or more specimens.

    Sheets: Summary, Cycles, Statistics (only with >1 specimen), Data
    dictionary, Config.
    """
    if not payloads:
        raise ValueError("write_workbook needs at least one specimen payload")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    book = xlsxwriter.Workbook(str(path), {"nan_inf_to_errors": True})

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

    _write_summary(book, f, payloads)
    _write_cycles(book, f, numfmt, payloads)
    _write_statistics(book, f, numfmt, payloads)
    _write_dictionary(book, f, payloads)
    _write_config(book, f, payloads)

    book.close()
    return path


def _write_summary(book, f, payloads: Sequence[dict]) -> None:
    """Fields down the page, specimens across it: readable for one specimen,
    directly comparable for the two that a series export produces."""
    sheet = book.add_worksheet("Summary")
    sheet.write(0, 0, "Compression test summary", f["title"])

    columns = [summary_pairs(p) for p in payloads]
    labels = [p.get("specimen", {}).get("label", f"Specimen {i + 1}")
              for i, p in enumerate(payloads)]

    row0 = 2
    sheet.write(row0, 0, "Field", f["head"])
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
        sheet.write(r, 0, "Read this before quoting the numbers", f["section"])
        last_col = max(1, len(labels))
        for w in warnings:
            r += 1
            style = f["bad"] if w["severity"] == "critical" else (
                f["warn"] if w["severity"] == "caution" else f["text"])
            sheet.write(r, 0, w["severity"].upper(), style)
            if last_col > 1:
                sheet.merge_range(r, 1, r, last_col, w["message"], f["wrap"])
            else:
                sheet.write(r, 1, w["message"], f["wrap"])

    sheet.set_column(0, 0, 34)
    sheet.set_column(1, len(labels), 34)
    sheet.freeze_panes(row0 + 1, 1)


def _has_value(columns: Sequence[list[tuple[str, Any]]], idx: int) -> bool:
    for pairs in columns:
        if idx < len(pairs) and pairs[idx][1] not in ("", None):
            return True
    return False


def _write_cycles(book, f, numfmt, payloads: Sequence[dict]) -> None:
    sheet = book.add_worksheet("Cycles")
    multi = len(payloads) > 1
    has_strain = any(p.get("analysis", {}).get("has_strain") for p in payloads)
    cols = user_facing_cycle_columns(has_strain)

    col0 = 1 if multi else 0
    if multi:
        sheet.write(0, 0, "Specimen", f["head"])

    for i, col in enumerate(cols):
        sheet.write(0, col0 + i, col.header, f["head"])

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
        sheet.set_column(col0 + i, col0 + i, _width_for(col, widths[i]))

    sheet.set_row(0, 46)
    sheet.freeze_panes(1, col0 + 1)
    if r > 1:
        sheet.autofilter(0, 0, r - 1, col0 + len(cols) - 1)


def _write_dictionary(book, f, payloads: Sequence[dict]) -> None:
    sheet = book.add_worksheet("Data dictionary")
    has_strain = any(p.get("analysis", {}).get("has_strain") for p in payloads)

    sheet.write(0, 0, "What each column means", f["title"])
    r = 2
    for header in ("Column", "Unit", "Definition"):
        sheet.write(r, ("Column", "Unit", "Definition").index(header), header, f["head"])
    r += 1

    sheet.write(r, 0, "Per-cycle columns", f["section"])
    r += 1
    for col in user_facing_cycle_columns(has_strain):
        sheet.write(r, 0, col.label, f["text"])
        sheet.write(r, 1, col.unit or "-", f["unit"])
        sheet.write(r, 2, col.description, f["wrap"])
        r += 1

    r += 1
    sheet.write(r, 0, "Summary fields", f["section"])
    r += 1
    for field in SPECIMEN_FIELDS:
        sheet.write(r, 0, field.label, f["text"])
        sheet.write(r, 1, field.unit or "-", f["unit"])
        sheet.write(r, 2, field.description, f["wrap"])
        r += 1

    sheet.set_column(0, 0, 38)
    sheet.set_column(1, 1, 10)
    sheet.set_column(2, 2, 96)
    sheet.freeze_panes(3, 0)


def _write_config(book, f, payloads: Sequence[dict]) -> None:
    """The settings behind the numbers, so a result can be reproduced or
    challenged without hunting for the script that produced it."""
    sheet = book.add_worksheet("Config")
    sheet.write(0, 0, "Analysis settings used", f["title"])
    sheet.write(2, 0, "Setting", f["head"])
    sheet.write(2, 1, "Value", f["head"])

    cfg = payloads[0].get("config", {})
    r = 3
    for key, value in cfg.items():
        sheet.write(r, 0, key, f["key"])
        if value is None:
            # A None knob is not missing data: it means the engine derives the
            # level from the test itself.
            sheet.write_string(r, 1, "auto", f["text"])
        else:
            _write_value(sheet, r, 1, value, f["text"], f["num"])
        r += 1

    differing = [p for p in payloads[1:] if p.get("config") != cfg]
    if differing:
        r += 1
        sheet.write(r, 0, "Warning: specimens in this workbook were analysed "
                          "with different settings.", f["section"])

    r += 2
    sheet.write(r, 0, "Derived reference levels", f["section"])
    r += 1
    for payload in payloads:
        analysis = payload.get("analysis", {})
        label = payload.get("specimen", {}).get("label", "")
        sheet.write(r, 0, f"{label}: reference stress (MPa)", f["key"])
        _write_value(sheet, r, 1, analysis.get("ref_stress_mpa"), f["text"], f["num"])
        r += 1
        sheet.write(r, 0, f"{label}: residual reference stress (MPa)", f["key"])
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
    "Stiffness_common_MPa_per_mm",
    "HysteresisLoss_rel",
    "PermDef_cumulative_mm",
    "Creep_during_hold_mm",
)
STATS_COLUMNS_STRAIN: tuple[str, ...] = ("PermDef_cumulative_pct",)


def cross_specimen_stats(payloads: Sequence[dict]) -> list[dict]:
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
            "label": col.label if col else key,
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


def _write_statistics(book, f, numfmt, payloads: Sequence[dict]) -> None:
    """Mean / std / CoV per cycle across specimens -- the same shape as the
    source export's own Statistik sheet (x / s / n[%]), extended to every
    cycle rather than a single Fmax reading."""
    stats = cross_specimen_stats(payloads)
    if not stats:
        return
    n_specimens = len({p.get("specimen", {}).get("label", "") for p in payloads})

    sheet = book.add_worksheet("Statistics")
    sheet.write(0, 0, "Cross-specimen statistics", f["title"])
    sheet.write(1, 0, f"n = {n_specimens} specimens", f["text"])

    r = 3
    for entry in stats:
        header = entry["label"] + (f" ({entry['unit']})" if entry["unit"] else "")
        sheet.write(r, 0, header, f["section"])
        r += 1
        for i, h in enumerate(("Cycle", "Mean", "Std dev", "CoV (%)")):
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


def cycles_dataframe(payloads: Sequence[dict], *, with_specimen: bool = False):
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
                record["Specimen"] = label
            for col, value in zip(cols, row_values(row, cols)):
                record[col.header] = value
            rows.append(record)
    return pd.DataFrame(rows)


def write_csv(payloads: Sequence[dict], path: str | Path, *, with_specimen: bool = False) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cycles_dataframe(payloads, with_specimen=with_specimen).to_csv(path, index=False)
    return path


__all__ = [
    "write_workbook",
    "write_csv",
    "cycles_dataframe",
    "cross_specimen_stats",
    "row_values",
    "summary_pairs",
]
