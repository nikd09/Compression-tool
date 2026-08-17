"""
schema.py
=========
Single source of truth for the shape of a persisted analysis.

Every downstream artefact -- the JSON on disk, the SQLite index, the Excel
workbook, the HTML report -- is generated from the specs below. A metric added
to the engine is therefore described in exactly one place and can never appear
under one name in the database and a different one in the spreadsheet.

Descriptions are deliberately verbose: they are surfaced verbatim in the
workbook's data-dictionary sheet, which is the only place a reader of the Excel
file learns *why* two stiffness columns exist and which one is comparable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

SCHEMA_VERSION = 1


# ----------------------------------------------------------------------------
# Column spec
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """One metric column, described once and reused everywhere."""

    key: str            # exact column name produced by core.analyse_test()
    sql: str            # SQLite storage class
    label: str          # human-readable header
    unit: str = ""      # empty when dimensionless
    description: str = ""
    fmt: str = "0.0000"  # Excel number format
    strain: bool = False   # only produced when h0 is known
    internal: bool = False  # kept in JSON/DB, hidden from user-facing tables

    @property
    def header(self) -> str:
        return f"{self.label} ({self.unit})" if self.unit else self.label


# ----------------------------------------------------------------------------
# Per-cycle metrics
# ----------------------------------------------------------------------------

CYCLE_COLUMNS: tuple[Column, ...] = (
    Column(
        "Cycle", "INTEGER", "Cycle", "",
        "Sequential cycle number within the test, counted after noise and "
        "part-cycles have been discarded.",
        fmt="0",
    ),
    Column(
        "PeakStress_MPa", "REAL", "Peak stress", "MPa",
        "Highest stress reached in this cycle. In a multi-stage test this "
        "rises from cycle to cycle, which is why several metrics below exist "
        "in both a per-cycle and a common-reference form.",
        fmt="0.00",
    ),
    Column(
        "PeakDisp_mm", "REAL", "Peak displacement", "mm",
        "Largest displacement reached in this cycle. It occurs at the END of "
        "the dwell, later than the stress peak, because the specimen keeps "
        "creeping while stress is already held constant.",
    ),
    Column(
        "ResidualDisp_mm", "REAL", "Residual displacement", "mm",
        "Displacement on the LOADING branch at the low common reference "
        "stress. Deliberately not read at zero stress: at zero the specimen "
        "loses contact and the signal falls back to an unloaded baseline of a "
        "few micrometres, which makes a zero-referenced permanent set "
        "meaningless.",
    ),
    Column(
        "PermDef_cumulative_mm", "REAL", "Permanent deformation, cumulative", "mm",
        "Residual displacement referenced to cycle 1. NOT compression set in "
        "the ASTM D395 / ISO 815 sense -- those are long-duration static "
        "tests. Do not label this 'compression set' in reports.",
    ),
    Column(
        "PermDef_incremental_mm", "REAL", "Permanent deformation, incremental", "mm",
        "Residual displacement gained relative to the preceding cycle. Blank "
        "for cycle 1, which has no predecessor.",
    ),
    Column(
        "Stiffness_common_MPa_per_mm", "REAL", "Stiffness (common band)", "MPa/mm",
        "Slope of the loading branch fitted over an IDENTICAL stress window in "
        "every cycle (25-75% of the smallest cycle peak in the test). This is "
        "the form that may be compared across stages, specimens and materials.",
        fmt="0.0",
    ),
    Column(
        "Stiffness_common_n", "INTEGER", "Stiffness (common band), points", "",
        "Number of samples the common-band fit used. A slope from a handful of "
        "points on a fast machine ramp is not trustworthy; treat fits below "
        "about 10 points as indicative only.",
        fmt="0",
    ),
    Column(
        "Stiffness_common_r2", "REAL", "Stiffness (common band), R2", "",
        "Coefficient of determination of the common-band fit. Low values mean "
        "the branch was curved over the window, so a single slope does not "
        "describe it.",
        fmt="0.000",
    ),
    Column(
        "Stiffness_relative_MPa_per_mm", "REAL", "Stiffness (relative band)", "MPa/mm",
        "Slope fitted over 25-75% of THIS cycle's own peak. Describes the "
        "cycle faithfully but rises artificially as the stages climb, so it is "
        "NOT comparable across cycles of a multi-stage test.",
        fmt="0.0",
    ),
    Column(
        "Stiffness_relative_n", "INTEGER", "Stiffness (relative band), points", "",
        "Number of samples the relative-band fit used.",
        fmt="0",
    ),
    Column(
        "Stiffness_relative_r2", "REAL", "Stiffness (relative band), R2", "",
        "Coefficient of determination of the relative-band fit.",
        fmt="0.000",
    ),
    Column(
        "DispAtRef_load_mm", "REAL", "Displacement at reference stress, loading", "mm",
        "Displacement where the loading branch crosses the reference stress. "
        "The reference is tied to the smallest cycle peak so that it is "
        "reachable in every cycle of a multi-stage test.",
    ),
    Column(
        "DispAtRef_unload_mm", "REAL", "Displacement at reference stress, unloading", "mm",
        "Displacement where the unloading branch crosses the same reference "
        "stress. The gap to the loading value is the width of the hysteresis "
        "loop at that stress.",
    ),
    Column(
        "Energy_in_MPa_mm", "REAL", "Energy in", "MPa*mm",
        "Work per unit volume put into the specimen along the loading path. "
        "The loop is split at maximum DISPLACEMENT, not maximum stress.",
        fmt="0.000",
    ),
    Column(
        "Energy_dissipated_MPa_mm", "REAL", "Energy dissipated", "MPa*mm",
        "Work per unit volume not recovered on unloading. Splitting the loop "
        "at maximum stress instead of maximum displacement drives this "
        "negative, which is physically impossible.",
        fmt="0.000",
    ),
    Column(
        "HysteresisLoss_rel", "REAL", "Hysteresis loss", "-",
        "Dissipated divided by input energy. This is the cross-test comparable "
        "form: absolute loss scales with stress amplitude, so a 50 MPa and a "
        "450 MPa stage cannot be compared on the absolute number.",
        fmt="0.0000",
    ),
    Column(
        "HoldDetected", "INTEGER", "Hold detected", "",
        "Whether a dwell plateau was found at peak stress in this cycle. "
        "Detected from the data, never asked about.",
        fmt="0",
    ),
    Column(
        "HoldPoints", "INTEGER", "Hold length", "samples",
        "Length of the detected dwell in SAMPLES, not seconds -- neither "
        "export carries a time channel. Creep rate therefore cannot be "
        "computed, only total creep across the dwell.",
        fmt="0",
    ),
    Column(
        "Creep_during_hold_mm", "REAL", "Creep during hold", "mm",
        "Displacement gained across the detected dwell. Left blank, not zero, "
        "when the cycle has no dwell.",
    ),
    # --- strain-normalised variants, only when h0 is known -------------------
    Column(
        "PeakStrain_pct", "REAL", "Peak strain", "%",
        "Peak displacement divided by the specimen height h0.",
        fmt="0.000", strain=True,
    ),
    Column(
        "PermDef_cumulative_pct", "REAL", "Permanent deformation, cumulative", "%",
        "Cumulative permanent deformation as a fraction of h0.",
        fmt="0.000", strain=True,
    ),
    Column(
        "PermDef_incremental_pct", "REAL", "Permanent deformation, incremental", "%",
        "Incremental permanent deformation as a fraction of h0.",
        fmt="0.000", strain=True,
    ),
    Column(
        "Creep_pct", "REAL", "Creep during hold", "%",
        "Creep across the dwell as a fraction of h0.",
        fmt="0.000", strain=True,
    ),
    # --- internal bookkeeping ------------------------------------------------
    Column(
        "_start", "INTEGER", "Start index", "",
        "Index of the cycle's first sample in the raw signal.",
        fmt="0", internal=True,
    ),
    Column(
        "_end", "INTEGER", "End index", "",
        "Index of the cycle's last sample in the raw signal.",
        fmt="0", internal=True,
    ),
)

CYCLE_BY_KEY: dict[str, Column] = {c.key: c for c in CYCLE_COLUMNS}


# ----------------------------------------------------------------------------
# Derived quality flag
# ----------------------------------------------------------------------------

# A stiffness slope is only as good as the fit behind it. The engine already
# reports n and R2; this turns the pair into one word the reader can act on,
# so a thin fit is never plotted or quoted as if it were solid.
QUALITY_MIN_POINTS = 10
QUALITY_MIN_R2 = 0.95

STIFFNESS_QUALITY = Column(
    "Stiffness_common_quality", "TEXT", "Stiffness (common band), quality", "",
    f"Fit quality of the common-band stiffness: 'ok', 'few points' "
    f"(n < {QUALITY_MIN_POINTS}), 'nonlinear' (R2 < {QUALITY_MIN_R2}), or "
    f"'none' when no fit was possible. Derived from the two columns before it.",
    fmt="@",
)


def stiffness_quality(n: Optional[float], r2: Optional[float]) -> str:
    """Collapse (n, R2) into a single word. Order matters: too few points is
    the more fundamental complaint, so it is reported ahead of nonlinearity."""
    if n is None or r2 is None:
        return "none"
    if n < QUALITY_MIN_POINTS:
        return "few points"
    if r2 < QUALITY_MIN_R2:
        return "nonlinear"
    return "ok"


# ----------------------------------------------------------------------------
# Per-specimen fields
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    key: str
    sql: str
    label: str
    unit: str = ""
    description: str = ""

    @property
    def header(self) -> str:
        return f"{self.label} ({self.unit})" if self.unit else self.label


SPECIMEN_FIELDS: tuple[Field, ...] = (
    Field("specimen_id", "TEXT", "Specimen ID", "",
          "Stable identifier derived from the source file's content hash and "
          "the specimen label. Survives a database rebuild."),
    Field("label", "TEXT", "Specimen", "", "Specimen label from the export."),
    Field("material", "TEXT", "Material", "", "Material name given at ingest."),
    Field("source_file", "TEXT", "Source file", "",
          "Path of the file as supplied at ingest."),
    Field("source_format", "TEXT", "Export format", "",
          "'series' for a multi-sample workbook, 'single' for one sample per sheet."),
    Field("source_sha256", "TEXT", "Source SHA-256", "",
          "Content hash of the original export, linking this record to the "
          "immutable copy in raw_input/."),
    Field("raw_input_path", "TEXT", "Archived copy", "",
          "Path of the immutable copy of the source export."),
    Field("displacement_channel", "TEXT", "Displacement channel", "",
          "Channel the displacement signal was taken from. The extensometer is "
          "preferred over the crosshead, which includes machine compliance."),
    Field("h0_mm", "REAL", "Specimen height h0", "mm",
          "Initial height. Read from the export's metadata sheet when present; "
          "without it, strain-normalised columns are suppressed rather than faked."),
    Field("d0_mm", "REAL", "Specimen diameter d0", "mm", "Initial diameter."),
    Field("temperature_c", "REAL", "Temperature", "degC", "Test temperature."),
    Field("n_points", "INTEGER", "Samples", "",
          "Number of valid samples in the cleaned signal."),
    Field("n_cycles", "INTEGER", "Cycles", "", "Number of cycles analysed."),
    Field("global_peak_mpa", "REAL", "Global peak stress", "MPa",
          "Highest stress reached anywhere in the test."),
    Field("multi_stage", "INTEGER", "Multi-stage", "",
          "True when cycle peaks vary by more than 5% of the global peak, i.e. "
          "the cycles are stages rather than repeats and must not be averaged."),
    Field("ref_stress_mpa", "REAL", "Reference stress", "MPa",
          "Common stress at which cross-cycle displacements are read."),
    Field("residual_stress_mpa", "REAL", "Residual reference stress", "MPa",
          "Low common stress at which residual displacement is read."),
    Field("created_utc", "TEXT", "Analysed at", "UTC", "Timestamp of the analysis run."),
    Field("run_dir", "TEXT", "Run directory", "", "Output folder for this run."),
    Field("json_path", "TEXT", "Record", "", "Path of the specimen's JSON record."),
)

SPECIMEN_BY_KEY: dict[str, Field] = {f.key: f for f in SPECIMEN_FIELDS}


def user_facing_cycle_columns(has_strain: bool) -> list[Column]:
    """Columns shown in Excel / CSV / HTML, in spec order, with the derived
    quality flag inserted directly after the fit it describes."""
    out: list[Column] = []
    for col in CYCLE_COLUMNS:
        if col.internal:
            continue
        if col.strain and not has_strain:
            continue
        out.append(col)
        if col.key == "Stiffness_common_r2":
            out.append(STIFFNESS_QUALITY)
    return out
