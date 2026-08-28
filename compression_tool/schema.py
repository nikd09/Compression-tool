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

# ----------------------------------------------------------------------------
# Frozen contract
# ----------------------------------------------------------------------------
# Version 2 is the contract the UI is built against. From here on the key sets
# below are additive-only within a version: a new key may be added, an existing
# key may NOT be renamed, retyped, or removed without a version bump, because
# every consumer -- workbook, report, SQLite index, UI -- reads them by name.
#
# tests/test_json_contract.py pins the exact key set at every level, so any
# change to the shape of a record has to be a deliberate edit to that test
# rather than something that slips through.
SCHEMA_VERSION = 2

# Top-level sections of a specimen record.
CONTRACT_TOP_LEVEL: tuple[str, ...] = (
    "schema_version", "created_utc", "specimen", "analysis", "config", "cycles",
)

CONTRACT_SPECIMEN: tuple[str, ...] = (
    "specimen_id", "label", "material", "source_filename", "source_file",
    "source_format", "source_sha256", "raw_input_path", "displacement_channel",
    "h0_mm", "d0_mm", "temperature_c", "n_points", "notes",
)

CONTRACT_ANALYSIS: tuple[str, ...] = (
    "n_cycles", "global_peak_mpa", "multi_stage", "ref_stress_mpa",
    "residual_stress_mpa", "h0_mm", "has_strain", "notes",
    "strain_basis", "warnings",
    # Common-band stiffness window, auto-located once on the reference
    # cycle and reused as absolute MPa bounds on every cycle (core.py) --
    # a test-wide pair of bounds, not a per-cycle value, so it lives here
    # rather than as a cycle column.
    "stiffness_common_lo_mpa", "stiffness_common_hi_mpa",
)

CONTRACT_STRAIN_BASIS: tuple[str, ...] = (
    "h0_mm", "displacement_channel", "gauge_length_confirmed", "strain_valid",
)

CONTRACT_WARNING: tuple[str, ...] = ("code", "severity", "message")

# Warning codes a consumer may rely on existing. New codes may be added;
# existing codes keep their meaning.
CONTRACT_WARNING_CODES: tuple[str, ...] = (
    "gauge_length_unconfirmed",
    "no_gauge_length",
    "first_cycle_near_discard_threshold",
    "cycles_discarded_by_peak_filter",
    "variable_dwell_length",
    # Retired: the within-cycle permanent-deformation redesign (core.py)
    # removed the failure class this covered (a cross-cycle rebase onto
    # whichever cycle happened to reach the residual reference stress).
    # diagnostics.py no longer emits it; the code is kept here only so an
    # older stored record's warning list still validates against this
    # contract.
    "first_cycle_residual_unreachable",
    "residual_unreadable_cycles",
    "residual_reference_not_low",
)


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
        "PeakDisp_mm", "REAL", "Displacement at peak stress", "mm",
        "Displacement at the instant of MAXIMUM STRESS -- not the largest "
        "displacement in the cycle. The two differ: the specimen keeps "
        "creeping through the dwell while stress is already held constant, so "
        "displacement goes on rising after stress has peaked. On a long dwell "
        "the maximum displacement can exceed this value by 20% or more. The "
        "energy integrals below split the loop at that later maximum, not "
        "here.",
    ),
    Column(
        "MaxDisp_mm", "REAL", "Maximum displacement", "mm",
        "Largest displacement reached in the cycle, and the point the energy "
        "integrals split the loop at. This is the figure to quote for how far "
        "the specimen actually moved. It exceeds the displacement at peak "
        "stress by however much the specimen crept while the load was held. "
        "It is NOT necessarily at the end of the dwell -- check the stress at "
        "maximum displacement beside it.",
    ),
    Column(
        "StressAtMaxDisp_MPa", "REAL", "Stress at maximum displacement", "MPa",
        "Stress at the instant the specimen was most compressed. On an intact "
        "specimen this equals the peak stress: displacement stops growing once "
        "the load stops being held. When it falls BELOW the peak, the specimen "
        "went on compacting while the load was being REMOVED -- it is still "
        "yielding on the unloading ramp. That is a damage signature that no "
        "other column here carries, and it appears as a step rather than a "
        "drift, so it dates the onset more sharply than the stiffness "
        "rollover does.",
        fmt="0.00",
    ),
    Column(
        "ResidualDisp_mm", "REAL", "Residual displacement (loading)", "mm",
        "Displacement on the LOADING branch at the low common reference "
        "stress. Deliberately not read at zero stress: at zero the specimen "
        "loses contact and the signal falls back to an unloaded baseline of a "
        "few micrometres, which makes a zero-referenced permanent set "
        "meaningless.",
    ),
    Column(
        "ResidualDisp_unload_mm", "REAL", "Residual displacement (unloading)", "mm",
        "The SAME reference stress as ResidualDisp_mm, read on the UNLOADING "
        "branch of this cycle instead. The gap between the two, within one "
        "cycle, is the permanent set gained in that cycle -- see "
        "PermDef_incremental_mm.",
    ),
    Column(
        "PermDef_cumulative_mm", "REAL", "Permanent deformation, cumulative", "mm",
        "Running total of PermDef_incremental_mm. NOT compression set in the "
        "ASTM D395 / ISO 815 sense -- those are long-duration static tests. "
        "Do not label this 'compression set' in reports.",
    ),
    Column(
        "PermDef_incremental_mm", "REAL", "Permanent deformation, incremental", "mm",
        "ResidualDisp_unload_mm minus ResidualDisp_mm: how much residual "
        "displacement THIS cycle gained between being read on the way up and "
        "read again on the way down, at the identical reference stress both "
        "times. Well-defined for a single-cycle test -- it needs no other "
        "cycle to compare against.",
    ),
    Column(
        "Stiffness_common_MPa_per_mm", "REAL", "Stiffness (common band)", "MPa/mm",
        "Slope of the loading branch fitted over an IDENTICAL stress window in "
        "every cycle -- auto-located once, on the reference (smallest-peak) "
        "cycle's own loading branch (see Stiffness_common_lo_MPa / _hi_MPa), "
        "then reused as the same absolute MPa bounds on every other cycle. "
        "This is the form that may be compared across stages, specimens and "
        "materials.",
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
        "Stiffness_common_lo_MPa", "REAL", "Stiffness (common band), window low", "MPa",
        "Lower bound of the auto-located common-band window, in absolute MPa "
        "-- identical on every cycle of this specimen. Reported so the "
        "automatic choice stays auditable rather than a black box.",
        fmt="0.00", internal=True,
    ),
    Column(
        "Stiffness_common_hi_MPa", "REAL", "Stiffness (common band), window high", "MPa",
        "Upper bound of the auto-located common-band window, in absolute MPa "
        "-- identical on every cycle of this specimen.",
        fmt="0.00", internal=True,
    ),
    Column(
        "Stiffness_relative_MPa_per_mm", "REAL", "Stiffness (relative band)", "MPa/mm",
        "Slope fitted over a window auto-located on THIS cycle's own loading "
        "branch (see Stiffness_relative_lo_MPa / _hi_MPa) -- the region of "
        "maximum, most-linear slope, found from the data rather than a fixed "
        "percentage (ASTM E111 toe compensation / chord modulus). Describes "
        "the cycle faithfully but is NOT comparable across cycles of a "
        "multi-stage test, since the window itself moves with each cycle's "
        "own peak.",
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
        "Stiffness_relative_lo_MPa", "REAL", "Stiffness (relative band), window low", "MPa",
        "Lower bound of the auto-located relative-band window for THIS cycle, "
        "in absolute MPa -- moves cycle to cycle, unlike the common band's.",
        fmt="0.00", internal=True,
    ),
    Column(
        "Stiffness_relative_hi_MPa", "REAL", "Stiffness (relative band), window high", "MPa",
        "Upper bound of the auto-located relative-band window for THIS cycle, "
        "in absolute MPa.",
        fmt="0.00", internal=True,
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
        "Work put into the specimen along the loading path, per unit CROSS-"
        "SECTIONAL AREA -- integral of stress over displacement is work/area, "
        "not work/volume. Divide by h0 to get work per unit volume in MPa. "
        "The loop is split at maximum DISPLACEMENT, not maximum stress.",
        fmt="0.000",
    ),
    Column(
        "Energy_dissipated_MPa_mm", "REAL", "Energy dissipated", "MPa*mm",
        "Work per unit cross-sectional area not recovered on unloading; "
        "divide by h0 for work per unit volume. Splitting the loop at maximum "
        "stress instead of maximum displacement drives this negative, which is "
        "physically impossible.",
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
        "Length of the detected dwell in SAMPLES, not seconds -- the export "
        "carries no time channel. This column is meaningless on its own and "
        "mandatory alongside the hold displacement beside it: without it there "
        "is no way to tell a specimen that moved further from one that was "
        "simply held longer.",
        fmt="0",
    ),
    Column(
        "Creep_during_hold_mm", "REAL", "Hold displacement", "mm",
        "Displacement accumulated across the detected dwell -- a TOTAL, not a "
        "rate. Left blank, not zero, when the cycle has no dwell. Only "
        "comparable between cycles whose hold lengths match, which is why the "
        "hold length is always reported next to it; a longer dwell accumulates "
        "more displacement at identical material behaviour.",
    ),
    # --- strain-normalised variants, only when h0 is known -------------------
    Column(
        "PeakStrain_pct", "REAL", "Strain at peak stress", "%",
        "Displacement at peak stress divided by the specimen height h0. "
        "PROVISIONAL unless the gauge length has been confirmed -- see the "
        "strain basis in the summary. Also taken at maximum stress rather than "
        "maximum displacement, so it understates how far the specimen moved.",
        fmt="0.000", strain=True,
    ),
    Column(
        "MaxStrain_pct", "REAL", "Maximum strain", "%",
        "Maximum displacement divided by h0 -- the largest strain the specimen "
        "reached. PROVISIONAL unless the gauge length has been confirmed.",
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
        "Creep_pct", "REAL", "Hold displacement (of h0)", "%",
        "Hold displacement as a fraction of h0. Still a total, not a rate, and "
        "still only comparable at matching hold lengths. PROVISIONAL unless "
        "the gauge length has been confirmed.",
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


HOLD_DISP_RATE = Column(
    "HoldDisp_per_1000_samples_mm", "REAL",
    "Hold displacement per 1000 samples", "mm",
    "Hold displacement divided by hold length. This exists ONLY to remove the "
    "distortion of unequal dwell lengths so cycles can be ranked against each "
    "other. It is NOT a creep rate and must never be labelled, plotted or "
    "quoted as one: converting samples to time needs a constant sampling "
    "interval, which the export does not record and which testXpert does not "
    "guarantee. A rate in mm/s requires a time channel enabled at export.",
    fmt="0.000000",
)


UNLOAD_YIELD = Column(
    "UnloadYield_frac", "REAL", "Stress at max displacement, of peak", "-",
    "Stress at maximum displacement divided by the cycle's peak stress. "
    "About 1.00 means the specimen stopped compacting when the load stopped "
    "being held -- intact behaviour. Ripple during the dwell puts an intact "
    "cycle a shade under 1.000 rather than exactly on it, so read roughly "
    "0.997 and above as intact. Below that the specimen kept compacting while "
    "the load was being REMOVED, and the shortfall measures how far into the "
    "unloading ramp that continued. Derived from the two columns before it.",
    fmt="0.000",
)


def unload_yield_frac(
    stress_at_max_disp: Optional[float], peak_stress: Optional[float]
) -> Optional[float]:
    """How far into unloading the specimen was still compacting, as a fraction
    of peak stress. 1.0 is intact; lower means still yielding as load came off."""
    if stress_at_max_disp is None or not peak_stress:
        return None
    return float(stress_at_max_disp) / float(peak_stress)


def hold_disp_per_1000_samples(
    hold_disp_mm: Optional[float], hold_points: Optional[float]
) -> Optional[float]:
    """Dwell-length-normalised hold displacement. Deliberately not a rate --
    see HOLD_DISP_RATE.description for why the distinction matters."""
    if hold_disp_mm is None or not hold_points:
        return None
    return float(hold_disp_mm) / float(hold_points) * 1000.0


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
    Field("source_filename", "TEXT", "Source file", "",
          "Original filename of the export, e.g. 'Mehrstufiger_....xlsx'. "
          "This is what an operator should read as 'which file was this'."),
    Field("source_file", "TEXT", "Source path (ingest machine)", "",
          "Full path as supplied at ingest, on whatever machine ran the "
          "ingest -- may be a temporary or session-specific path and is not "
          "meaningful to a reader on a different machine. Kept for "
          "provenance; use source_filename or raw_input_path for anything "
          "operator-facing."),
    Field("source_format", "TEXT", "Export format", "",
          "'series' for a multi-sample workbook, 'single' for one sample per sheet."),
    Field("source_sha256", "TEXT", "Source SHA-256", "",
          "Content hash of the original export, linking this record to the "
          "immutable copy in Raw exports/."),
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
    """Columns shown in Excel / CSV / HTML, in spec order, with each derived
    column inserted directly after the column it qualifies.

    Adjacency is not cosmetic here. The quality flag is meaningless away from
    the fit it describes, and the hold displacement is meaningless away from
    the hold length -- separating either pair invites exactly the misreading
    it exists to prevent.
    """
    out: list[Column] = []
    for col in CYCLE_COLUMNS:
        if col.internal:
            continue
        if col.strain and not has_strain:
            continue
        out.append(col)
        if col.key == "Stiffness_common_r2":
            out.append(STIFFNESS_QUALITY)
        if col.key == "StressAtMaxDisp_MPa":
            out.append(UNLOAD_YIELD)
        if col.key == "Creep_during_hold_mm":
            out.append(HOLD_DISP_RATE)
    return out


# Pairs that must never be separated in any view. The UI is free to reorder or
# hide columns, but splitting one of these pairs strips a number of the context
# that makes it readable.
INSEPARABLE_PAIRS: tuple[tuple[str, str], ...] = (
    ("Stiffness_common_r2", "Stiffness_common_quality"),
    ("StressAtMaxDisp_MPa", "UnloadYield_frac"),
    ("HoldPoints", "Creep_during_hold_mm"),
    ("Creep_during_hold_mm", "HoldDisp_per_1000_samples_mm"),
)
