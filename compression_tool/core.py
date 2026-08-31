"""
compression_core.py
===================
Format-agnostic ingestion + scientifically-grounded metrics for load-controlled
cyclic / multi-stage compression tests (Zwick Z100 Excel exports).

Design principles
-----------------
1. NO interactive input. Everything is driven by a Config object so the engine
   can be called from a UI, a batch script, or a test suite.
2. NO absolute thresholds. Every threshold is relative to the test's own peak
   stress, so a 2 MPa test and a 450 MPa test run with identical defaults.
3. Multi-stage aware. Peak stress commonly RISES each cycle (50->100->...->450
   MPa). Metrics are therefore reported both per-cycle-relative AND at a common
   reference stress so cycles and materials stay comparable.
4. Hold periods are detected from the data by default, never asked about --
   except the yes/no of whether a hold exists at all (`Config.detect_holds`),
   for a fast-cycling test with no programmed dwell where turnaround at peak
   can otherwise be mistaken for one (see `detect_hold`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy.signal import find_peaks

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------


@dataclass
class Config:
    """All tuning knobs. Defaults are relative and should work unmodified."""

    # --- segmentation -------------------------------------------------------
    # Cycle boundaries are found by scipy.signal.find_peaks on stress
    # (see segment_cycles). Two independent, complementary checks decide
    # whether a candidate local peak is a genuine cycle, neither of them a
    # single fixed fraction of the whole test's global peak:
    #
    #   major_cycle_frac -- an absolute SAFETY FLOOR only, on the candidate's
    #   own peak height as a fraction of the global peak. It exists only to
    #   reject genuinely spurious near-zero blips (e.g. the machine finding
    #   contact at the start of a record), not to gate real stages -- a real
    #   stage is judged by the two checks below, not by how its height
    #   compares to some OTHER stage's height. Kept low by default so it
    #   stays out of the way of the smallest real stage in a multi-stage
    #   test, which by construction sits near 1/N of the global peak for an
    #   N-stage test and was a coin flip against the old default (0.10, which
    #   used to be the primary gate) for any 10-ish-stage test.
    #
    #   unload_frac -- RELATIVE PROMINENCE RATIO: the minimum fraction of a
    #   candidate's OWN peak height (above the true baseline) that its
    #   surrounding valley must give back for it to count as a distinct
    #   load-unload cycle, rather than a shoulder on the ramp toward a taller
    #   neighbouring peak. This is the standard fix, in topographic and
    #   general peak-detection practice, for exactly this ambiguity: a
    #   "prominence ratio" (prominence / height, sometimes called a peak's
    #   "key col ratio") well below 1 identifies a subsidiary bump that never
    #   really separated from its neighbour, regardless of how large that
    #   bump is in absolute terms. On the real, low-peak multi-stage files
    #   this was validated against, every genuine stage's ratio was >= 0.72
    #   (most >= 0.9, since the specimen normally unloads almost fully
    #   between stages) while a real transient ramp-overshoot artefact
    #   measured 0.135 -- a wide, unambiguous margin either side of 0.5.
    #
    # The candidate's actual local significance against ITS OWN neighbouring
    # noise (not a fixed fraction of anything) is a THIRD, separate check
    # inside segment_cycles -- these two fields are floors on top of it, not
    # a replacement for it.
    unload_frac: float = 0.5        # min prominence / candidate's own peak
    major_cycle_frac: float = 0.01  # candidate's own peak floor, * global peak
    min_cycle_points: int = 50

    # --- hold detection -----------------------------------------------------
    hold_tol_frac: float = 0.005   # +/- this * cycle peak counts as "at peak"
    hold_min_points: int = 20      # shorter plateau => no hold in this cycle
    # A fast-cycling test with NO programmed dwell still spends a handful of
    # samples turning around at peak stress -- geometry, not a hold -- and on
    # a short enough cycle that turnaround can accidentally clear
    # hold_min_points, misreading it as a real dwell. Set False for a test
    # known to have no hold rather than tuning hold_min_points per file.
    detect_holds: bool = True

    # --- stiffness ----------------------------------------------------------
    # FALLBACK window only: the real window is auto-located from the data on
    # every cycle (see _auto_stiffness_window / ASTM E111 toe compensation).
    # These two are used only on the rare cycle where no candidate window
    # clears the minimum span -- not a manual override of auto-detection,
    # which always runs first.
    stiff_lo_frac: float = 0.25    # fallback lower bound of regression window
    stiff_hi_frac: float = 0.75    # fallback upper bound of regression window

    # --- the one reference stress ------------------------------------------
    # A single low, test-wide stress, read on both branches of every cycle.
    # Used for two different jobs -- permanent deformation (ResidualDisp_mm /
    # ResidualDisp_unload_mm, see analyse_test) and cross-cycle comparison
    # (the same two columns, read cycle over cycle) -- which used to be two
    # separate constants (residual_stress at 0.02x global peak, ref_stress at
    # 0.50x the smallest held cycle's peak). Merged into one: a SEPARATE
    # mid-range reference is unreachable on exactly the small/single-cycle
    # tests this engine is built to still get right (T050E1's own cycle 1,
    # peak 9.96 MPa, never reaches a 25 MPa reference), while anchoring to the
    # global peak keeps this one low enough to be reachable everywhere and
    # still clear of the low-stress contact-loss noise confirmed on real data
    # (MeshG_3mpa_10cyc_3's cycle 1: a non-monotonic plateau at 0.75-0.85 MPa,
    # comfortably above 0.02x its 29.92 MPa global peak = 0.60 MPa).
    #
    # Measured on the loading branch, NOT at zero stress: at zero the
    # specimen loses contact with the platen and the signal returns to its
    # unloaded baseline, which makes a zero-referenced reading meaningless.
    residual_stress_mpa: Optional[float] = None
    residual_stress_frac: float = 0.02  # of global peak, if not set explicitly

    # --- specimen -----------------------------------------------------------
    # Fallback only. Real value is read from the export's metadata sheet when
    # present. Without it, strain-normalised outputs are suppressed (not faked).
    h0_mm: Optional[float] = None


# ----------------------------------------------------------------------------
# Data containers
# ----------------------------------------------------------------------------


@dataclass
class TestData:
    """One specimen: raw signal plus whatever metadata the export carried."""

    label: str
    displacement_mm: np.ndarray
    stress_mpa: np.ndarray
    source_file: str
    source_format: str
    h0_mm: Optional[float] = None
    d0_mm: Optional[float] = None
    temperature_c: Optional[float] = None
    displacement_channel: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def strain(self) -> Optional[np.ndarray]:
        if not self.h0_mm:
            return None
        return self.displacement_mm / self.h0_mm


# ----------------------------------------------------------------------------
# Unit handling
# ----------------------------------------------------------------------------

_DISP_UNITS = {"mm": 1.0, "µm": 1e-3, "um": 1e-3, "micron": 1e-3, "m": 1e3}
_STRESS_UNITS = {
    "mpa": 1.0,
    "n/mm²": 1.0,
    "n/mm2": 1.0,
    "kpa": 1e-3,
    "pa": 1e-6,
    "gpa": 1e3,
}


def _disp_factor(unit: str) -> float:
    u = (unit or "").strip().lower()
    for key, fac in _DISP_UNITS.items():
        if u == key.lower():
            return fac
    raise ValueError(f"Unrecognised displacement unit: {unit!r}")


def _stress_factor(unit: str) -> float:
    u = (unit or "").strip().lower().replace(" ", "")
    if u in _STRESS_UNITS:
        return _STRESS_UNITS[u]
    raise ValueError(f"Unrecognised stress unit: {unit!r}")


def _is_stress_unit(unit: str) -> bool:
    return (unit or "").strip().lower().replace(" ", "") in _STRESS_UNITS


def _is_disp_unit(unit: str) -> bool:
    return (unit or "").strip().lower() in {k.lower() for k in _DISP_UNITS}


# ----------------------------------------------------------------------------
# Format detection + loading
# ----------------------------------------------------------------------------

# Sheet names in the multi-sample export carry a trailing space in the wild
# ("Werte Serie "), so match loosely.
_SERIES_VALUES_RE = re.compile(r"^\s*werte\s+serie", re.I)
_SERIES_RESULTS_RE = re.compile(r"^\s*ergebnisse\s+serie", re.I)
_SERIES_PARAM_RE = re.compile(r"^\s*parameter\s+serie", re.I)


def detect_format(path: str) -> str:
    """Return 'series' (multi-sample workbook) or 'single' (one sample, one sheet)."""
    sheets = pd.ExcelFile(path, engine="openpyxl").sheet_names
    if any(_SERIES_VALUES_RE.match(s) for s in sheets):
        return "series"
    return "single"


def _find_sheet(sheets: list[str], pattern: re.Pattern) -> Optional[str]:
    for s in sheets:
        if pattern.match(s):
            return s
    return None


def _read_series_metadata(path: str, sheets: list[str]) -> dict[int, dict]:
    """Pull h0 / d0 / temperature per sample from 'Ergebnisse Serie'."""
    meta: dict[int, dict] = {}
    name = _find_sheet(sheets, _SERIES_RESULTS_RE)
    if not name:
        return meta
    raw = pd.read_excel(path, sheet_name=name, header=None, engine="openpyxl")
    if raw.empty:
        return meta

    headers = [str(v).strip().lower() for v in raw.iloc[0].tolist()]

    def col_for(*keys, prefer_unit: Optional[str] = None) -> Optional[int]:
        """First header containing any of `keys` as a substring.

        A plain substring match alone is too easy to fool: a header like
        "dh0/h0 in %" (a relative-deviation column some exports carry
        alongside the real measurement) also contains "h0" and, if it comes
        first, would silently bind h0_mm to a percentage instead of a
        length -- wrong strain and modulus, with no warning. `prefer_unit`
        makes a header that ALSO carries the expected unit win over one that
        does not, in one pass, before falling back to a bare substring match
        so a genuinely differently-worded but unambiguous header (only one
        candidate at all) still matches exactly as before.
        """
        candidates = [i for i, h in enumerate(headers) if any(k in h for k in keys)]
        if not candidates:
            return None
        if prefer_unit:
            for i in candidates:
                if prefer_unit in headers[i]:
                    return i
        return candidates[0]

    c_h0 = col_for("h0", prefer_unit="mm")
    c_d0 = col_for("d0", prefer_unit="mm")
    c_temp = col_for("temperatur")

    for _, row in raw.iloc[2:].iterrows():
        try:
            sample_no = int(float(row.iloc[0]))
        except (TypeError, ValueError):
            continue
        entry = {}
        for key, col in (("h0_mm", c_h0), ("d0_mm", c_d0), ("temperature_c", c_temp)):
            if col is not None:
                val = pd.to_numeric(row.iloc[col], errors="coerce")
                if pd.notna(val):
                    entry[key] = float(val)
        meta[sample_no] = entry
    return meta


def _clean_pair(disp_raw, stress_raw, d_fac, s_fac) -> tuple[np.ndarray, np.ndarray]:
    pair = pd.DataFrame({"d": disp_raw, "s": stress_raw}).apply(
        pd.to_numeric, errors="coerce"
    ).dropna()
    return pair["d"].to_numpy() * d_fac, pair["s"].to_numpy() * s_fac


def load_series_format(path: str, cfg: Config, *, label_stem: Optional[str] = None) -> list[TestData]:
    """Multi-sample workbook: 'Werte Serie' holds sample columns side by side.

    Layout (row 0 = sample no, row 1 = channel name, row 2 = unit, then data).
    Column pairs are (displacement, stress) per sample -- but which of the two
    is which is decided from the UNIT row, never from position.

    `label_stem` overrides the filename-derived stem every specimen's label
    is built from (see `_stem`) -- see `load_tests`'s docstring for why this
    exists.
    """
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheets = xl.sheet_names
    values_sheet = _find_sheet(sheets, _SERIES_VALUES_RE)
    if values_sheet is None:
        raise ValueError("No 'Werte Serie' sheet found")

    stem = label_stem if label_stem is not None else _stem(path)
    meta = _read_series_metadata(path, sheets)
    raw = pd.read_excel(path, sheet_name=values_sheet, header=None, engine="openpyxl")

    sample_ids = raw.iloc[0].tolist()
    channels = [str(v).strip() for v in raw.iloc[1].tolist()]
    units = [str(v).strip() for v in raw.iloc[2].tolist()]
    body = raw.iloc[3:].reset_index(drop=True)

    # group columns by sample id
    groups: dict[str, list[int]] = {}
    for i, sid in enumerate(sample_ids):
        if pd.isna(sid):
            continue
        groups.setdefault(str(sid).strip(), []).append(i)

    tests: list[TestData] = []
    for sid, cols in groups.items():
        d_col = next((c for c in cols if _is_disp_unit(units[c])), None)
        s_col = next((c for c in cols if _is_stress_unit(units[c])), None)
        if d_col is None or s_col is None:
            continue

        disp, stress = _clean_pair(
            body[d_col], body[s_col], _disp_factor(units[d_col]), _stress_factor(units[s_col])
        )
        if len(disp) < cfg.min_cycle_points:
            continue

        try:
            m = meta.get(int(float(sid)), {})
        except ValueError:
            m = {}

        tests.append(
            TestData(
                label=f"{stem}_S{sid}",
                displacement_mm=disp,
                stress_mpa=stress,
                source_file=path,
                source_format="series",
                displacement_channel=channels[d_col],
                h0_mm=m.get("h0_mm") or cfg.h0_mm,
                d0_mm=m.get("d0_mm"),
                temperature_c=m.get("temperature_c"),
            )
        )
    return tests


def load_single_format(path: str, cfg: Config, *, label_stem: Optional[str] = None) -> list[TestData]:
    """One sample per sheet: row 0 = title, row 1 = channel, row 2 = unit.

    Such exports often carry BOTH a crosshead channel (Standardweg) and an
    extensometer channel (Sonder LAA). The extensometer is preferred because
    the crosshead signal includes machine compliance.

    `label_stem` overrides the filename-derived label (see `_stem`) -- see
    `load_tests`'s docstring for why this exists.
    """
    raw = pd.read_excel(path, sheet_name=0, header=None, engine="openpyxl")
    channels = [str(v).strip() for v in raw.iloc[1].tolist()]
    units = [str(v).strip() for v in raw.iloc[2].tolist()]
    body = raw.iloc[3:].reset_index(drop=True)

    s_col = next((i for i, u in enumerate(units) if _is_stress_unit(u)), None)
    d_cols = [i for i, u in enumerate(units) if _is_disp_unit(u)]
    if s_col is None or not d_cols:
        raise ValueError(f"Could not identify stress/displacement columns in {path}")

    notes = []
    d_col = d_cols[0]
    for i in d_cols:
        if re.search(r"l[äa]a|extenso|sonder", channels[i], re.I):
            d_col = i
            break
    if len(d_cols) > 1:
        notes.append(
            f"{len(d_cols)} displacement channels found "
            f"({', '.join(channels[i] for i in d_cols)}); using '{channels[d_col]}'."
        )

    disp, stress = _clean_pair(
        body[d_col], body[s_col], _disp_factor(units[d_col]), _stress_factor(units[s_col])
    )
    return [
        TestData(
            label=label_stem if label_stem is not None else _stem(path),
            displacement_mm=disp,
            stress_mpa=stress,
            source_file=path,
            source_format="single",
            displacement_channel=channels[d_col],
            h0_mm=cfg.h0_mm,
            notes=notes,
        )
    ]


def load_tests(
    path: str, cfg: Optional[Config] = None, *, label_stem: Optional[str] = None
) -> list[TestData]:
    """Entry point: detect the export layout and return one TestData per specimen.

    `label_stem` overrides the filename-derived stem every specimen's label
    is built from -- every label is `{stem}` (single-sample exports) or
    `{stem}_S{n}` (multi-sample exports), so the specimen ID
    (persistence.specimen_id, hashed from source content + label + material)
    is only stable across two ingests of the SAME bytes if the stem is too.

    That is silently NOT the case for Config's "Re-analyse this run": it
    feeds back the archive's own copy of the source
    (`Raw exports/<sha12>_<slugified-original-name>.xlsx`, see
    persistence.archive_raw), whose filename -- both the added hash prefix
    and the slugified original name (spaces become hyphens, etc.) -- differs
    from whatever the file was named at the ORIGINAL ingest. Without this
    override, re-analysing produces a different label, therefore a different
    specimen ID, therefore a second, duplicate row in the index alongside
    the original instead of updating it in place -- confirmed live: exactly
    this, visible as two copies of the same specimen in the Results picker,
    one under the plain original name and one under
    "<hash>_Slugified-Original-Name_S1". Callers that already know the
    stem the ORIGINAL ingest used (pipeline.ingest, via its own
    `label_stems` parameter) pass it through here instead of letting it be
    re-derived from whatever path happens to be on disk now.
    """
    cfg = cfg or Config()
    fmt = detect_format(path)
    if fmt == "series":
        return load_series_format(path, cfg, label_stem=label_stem)
    return load_single_format(path, cfg, label_stem=label_stem)


def _stem(path: str) -> str:
    import os

    return os.path.splitext(os.path.basename(path))[0]


# ----------------------------------------------------------------------------
# Segmentation
# ----------------------------------------------------------------------------


# Robust noise-scale estimator: for approximately-Gaussian noise, 1.4826 *
# MAD is the standard consistent estimator of the standard deviation --
# used everywhere below instead of a raw std so a handful of true outliers
# (a real cycle's own rise) do not inflate the very "how noisy is this"
# number that is supposed to describe everything BUT those outliers.
_MAD_TO_SIGMA = 1.4826

# Multiplier for the real accept/reject decision: a "several sigma above the
# local noise floor" significance rule, the standard shape of an adaptive
# peak-detection threshold. Set above the more common 3-sigma rule of thumb
# because dwell ripple is itself locally periodic and a lower multiplier
# over-segmented a held plateau into several spurious small cycles when
# checked against this codebase's own synthetic and real test signals.
_ACCEPT_PROMINENCE_SIGMA = 5.0


def _robust_sigma(values: np.ndarray) -> float:
    """1.4826 * median absolute deviation -- an outlier-resistant proxy for a
    noisy signal's own standard deviation. Returns 0.0 for an empty or
    perfectly flat input; callers fall back to a coarser estimate in that
    case rather than treating 0.0 as "no noise, so anything is a peak"."""
    if values.size == 0:
        return 0.0
    med = float(np.median(values))
    return float(_MAD_TO_SIGMA * np.median(np.abs(values - med)))


def segment_cycles(stress: np.ndarray, cfg: Config) -> list[tuple[int, int]]:
    """Split into load-unload cycles by adaptive, locally-relative peak detection.

    A single global fraction of the test's peak stress cannot serve both a
    small early stage (which, in an evenly-staged test, sits near any such
    fraction by construction) and a deep dwell ripple later in the SAME
    signal -- one number is either too loose for the ripple or too tight for
    the early stage. `scipy.signal.find_peaks` is used instead, with each
    candidate peak accepted or rejected against a threshold derived from ITS
    OWN local neighbourhood's noise, not one global number. This is what lets
    the same defaults segment a 1-cycle and a 20-cycle test, a 3 MPa and a
    450 MPa test, without retuning.

    `find_peaks` finds every local maximum at least `min_cycle_points` apart
    -- nothing else, deliberately: on a signal with a dwell, that includes
    every ripple wiggle sitting on top of a single hold as its own candidate,
    alongside the genuine per-stage peaks. `scipy.signal.peak_prominences` is
    NOT used to tell them apart. Its base search looks outward for the
    nearest point that is strictly HIGHER than the candidate; when several
    same-height ripple wiggles sit next to each other, none of them is higher
    than any other, so the search walks straight through all of them and
    only stops at the true valley at the FAR ends of the whole dwell --
    reporting every individual ripple wiggle's prominence as the height of
    the ENTIRE stage, not the tiny wiggle it actually is (confirmed directly:
    a synthetic 3-ripple dwell produced three "cycles" at identical full
    stage height before this was caught). Bounding that search window
    (`wlen`) does not fix it either: the window has to be smaller than the
    ripple spacing to avoid the same problem, which makes it too small to
    reach a genuine cycle's own true boundary valley, which is typically far
    larger.

    Instead, adjacent raw candidates are merged PAIRWISE, nearest first: the
    valley between them is compared against an adaptive local-noise floor
    AND against `unload_frac` of the shorter candidate's own height (see
    `Config`'s docstring for both). If the valley does not clear both, it is
    not a genuine load-unload separation -- the shorter candidate is dropped
    and its taller neighbour absorbs it -- and the pair is re-tested against
    whatever is now adjacent, since dropping one candidate can expose a new
    pair that also needs judging. This directly answers "is this a real
    cycle boundary" using only each candidate's own immediate neighbourhood,
    which is exactly what a bounded `wlen` was trying (and failing) to give
    scipy's own algorithm. What survives this reduction is then checked
    against `major_cycle_frac`, an absolute floor on its own height (rejects
    near-zero contact-finding blips that have no real neighbour to be
    compared against).

    Boundaries are found the way this function has always found them: the
    nearest true local stress minimum on either side of each accepted peak
    -- the exact same argmin-based expansion used before this redesign, now
    seeded from the surviving peak indices instead of a boolean "loaded"
    run's start/end. A valley that only partially relaxes between two stages
    (instead of returning near zero) is still a real local minimum either
    way, so this expansion needs no change to handle it correctly.
    """
    n = len(stress)
    if n == 0:
        # A malformed or completely unparseable column reaches here as a
        # zero-length array; "no cycles" is the correct answer for it too.
        return []
    finite = np.isfinite(stress)
    if not finite.any():
        return []
    peak = float(np.max(stress[finite]))
    if peak <= 0:
        return []

    # A NaN/dropped sample is exactly as "unloaded" as a genuine near-zero
    # reading for segmentation purposes. It collapses to a floor value BELOW
    # every real sample rather than being imputed to any real stress.
    s = np.where(finite, stress, float(np.min(stress[finite])) - 1.0)

    safety_peak_floor = cfg.major_cycle_frac * peak
    min_dist = max(int(cfg.min_cycle_points), 1)
    # Radius of the window each valley's local noise is measured over. Fixed
    # to min_cycle_points rather than derived from inter-peak spacing: sensor
    # jitter and dwell ripple are both high-frequency, local phenomena,
    # visible in a handful of samples around any point, and confirmed (on
    # real data) to be mistaken for genuine signal slope -- inflating the
    # very threshold meant to reject only noise -- when the window is instead
    # sized off how far apart CYCLES happen to be.
    half = max(min_dist, 10)

    global_sigma = _robust_sigma(np.diff(s)) or float(np.std(np.diff(s))) or 1e-9

    candidates, _ = find_peaks(s, distance=min_dist)
    if candidates.size == 0:
        return []
    survivors = [int(c) for c in candidates]

    # Pairwise ripple/noise reduction -- see docstring.
    i = 0
    while i < len(survivors) - 1:
        a, b = survivors[i], survivors[i + 1]
        valley = float(np.min(s[a : b + 1]))
        shorter = min(float(s[a]), float(s[b]))
        lo_ctx, hi_ctx = max(0, a - half), min(n, b + half + 1)
        local_sigma = _robust_sigma(np.diff(s[lo_ctx:hi_ctx])) or global_sigma
        depth = shorter - valley
        is_real_separation = depth >= local_sigma * _ACCEPT_PROMINENCE_SIGMA and (
            shorter <= 0 or depth >= cfg.unload_frac * shorter
        )
        if is_real_separation:
            i += 1
            continue
        # Not a real separation between a and b -- drop the shorter of the
        # two and re-test from the same position, since the pair now
        # adjacent may also need merging.
        del survivors[i + (1 if s[a] >= s[b] else 0)]

    accepted = [p for p in survivors if float(s[p]) >= safety_peak_floor]
    if not accepted:
        return []

    # Expand each accepted peak outward to the nearest true local minimum on
    # either side -- unchanged arithmetic from before this redesign. Without
    # this the cycle would begin AT its peak's detection point, so the
    # loading branch could never be interpolated at any reference stress
    # below whatever level the peak was first distinguished at.
    out = []
    for k, p in enumerate(accepted):
        prev_end = accepted[k - 1] if k > 0 else 0
        next_start = accepted[k + 1] if k + 1 < len(accepted) else n - 1
        lo = prev_end + int(np.argmin(s[prev_end : p + 1])) if p > prev_end else p
        hi = p + int(np.argmin(s[p : next_start + 1])) if next_start > p else p
        out.append((lo, hi))

    # min_cycle_points is enforced on this EXPANDED boundary, not the bare
    # peak-to-peak gap find_peaks' `distance` used -- a real cycle's rise +
    # hold + fall is what has to clear this length, not just the spacing
    # between two detected peaks.
    return [(lo, hi) for lo, hi in out if hi - lo + 1 >= cfg.min_cycle_points]


def detect_hold(stress: np.ndarray, cfg: Config) -> Optional[tuple[int, int]]:
    """Find the dwell plateau at peak stress within a single cycle.

    Returns (start, end) indices of the plateau, or None when the cycle has no
    hold -- in which case creep metrics are omitted rather than computed from
    an arbitrary pair of points.
    """
    if not cfg.detect_holds:
        return None
    if len(stress) < cfg.hold_min_points:
        return None
    peak_idx = int(np.argmax(stress))
    peak = float(stress[peak_idx])
    tol = cfg.hold_tol_frac * peak

    lo = peak_idx
    while lo - 1 >= 0 and abs(stress[lo - 1] - peak) <= tol:
        lo -= 1
    hi = peak_idx
    while hi + 1 < len(stress) and abs(stress[hi + 1] - peak) <= tol:
        hi += 1

    if hi - lo + 1 < cfg.hold_min_points:
        return None
    return lo, hi


# ----------------------------------------------------------------------------
# Interpolation helpers
# ----------------------------------------------------------------------------


# How many samples after a candidate crossing must stay on the far side of
# `target` before it is accepted -- see _interp_on_branch. A single-sample
# non-monotonic ripple (stick-slip release, elastic snap-back as the
# specimen separates from the platen) is physically plausible near a low
# reference stress, more so on the unloading branch than the machine-driven
# loading branch, and would otherwise be mistaken for the genuine crossing.
_CROSSING_CONFIRM_SAMPLES = 3


def _interp_on_branch(
    stress: np.ndarray, disp: np.ndarray, target: float, branch: str
) -> Optional[float]:
    """Displacement where the branch crosses `target` stress (linear interp).

    The first candidate crossing is not accepted on sight: the next few
    samples must stay on the far side of `target` too (see
    `_CROSSING_CONFIRM_SAMPLES`), so a single noisy sample does not get
    mistaken for the real crossing while the branch has not actually reached
    `target` yet -- the search simply continues past a false one to the next
    candidate.
    """
    n = len(stress)
    if n < 2:
        return None
    peak_idx = int(np.argmax(stress))

    if branch == "loading":
        seg_s, seg_x = stress[: peak_idx + 1], disp[: peak_idx + 1]
        rising = True
    else:
        seg_s, seg_x = stress[peak_idx:], disp[peak_idx:]
        rising = False
    if len(seg_s) < 2:
        return None

    for i in range(1, len(seg_s)):
        a, b = seg_s[i - 1], seg_s[i]
        hit = (a < target <= b) if rising else (a > target >= b)
        if not hit:
            continue
        look = seg_s[i : i + _CROSSING_CONFIRM_SAMPLES]
        stays = bool(np.all(look >= target)) if rising else bool(np.all(look <= target))
        if not stays:
            continue
        if b == a:
            return float(seg_x[i])
        t = (target - a) / (b - a)
        return float(seg_x[i - 1] + t * (seg_x[i] - seg_x[i - 1]))
    return None


# ----------------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------------


def _fit_window(
    s_load: np.ndarray, x_load: np.ndarray, lo_mpa: float, hi_mpa: float
) -> tuple[Optional[float], int, Optional[float]]:
    """Regress stress on displacement over [lo_mpa, hi_mpa] of an ALREADY
    loading-branch-only pair of arrays. Returns (slope, n_points, r_squared)
    -- see `_stiffness`'s docstring for why the last two matter as much as
    the slope itself. Split out of `_stiffness` so `_auto_stiffness_window`'s
    grid search can slice the loading branch once and re-fit many candidate
    windows against it, instead of re-finding the peak index on every trial.
    """
    mask = (s_load >= lo_mpa) & (s_load <= hi_mpa)
    n = int(np.sum(mask))
    if n < 3:
        return None, n, None
    x_sel, s_sel = x_load[mask], s_load[mask]
    if np.ptp(x_sel) <= 0:
        return None, n, None
    slope, intercept = np.polyfit(x_sel, s_sel, 1)
    resid = s_sel - (slope * x_sel + intercept)
    ss_tot = float(np.sum((s_sel - s_sel.mean()) ** 2))
    r2 = float(1 - np.sum(resid**2) / ss_tot) if ss_tot > 0 else None
    return float(slope), n, r2


def _stiffness(stress, disp, lo_mpa, hi_mpa) -> tuple[Optional[float], int, Optional[float]]:
    """Slope d(stress)/d(disp) on the loading branch, MPa/mm, over a caller-
    supplied fixed window. Returns (slope, n_points, r_squared) -- see
    `_fit_window`. A one-window convenience wrapper kept for callers (and
    tests) that want a specific, pre-decided window rather than the
    auto-located one `_auto_stiffness_window` searches for.
    """
    peak_idx = int(np.argmax(stress))
    return _fit_window(stress[: peak_idx + 1], disp[: peak_idx + 1], lo_mpa, hi_mpa)


# --- stiffness: auto-located linear region (toe-compensated chord modulus) --
# ASTM E111 (Young's/Tangent/Chord Modulus) fits a chord modulus over a
# region of the curve, but does not prescribe a fixed percentage window for
# it -- the low-stress "toe" from seating/slack/alignment is nonlinear and
# its extent varies test to test, so the standard's own remedy (toe
# compensation) is to LOCATE the region of maximum, most-linear slope from
# the data itself. This grid search is that "locate" step.
_STIFF_MIN_SPAN_FRAC = 0.40  # candidate window must span >= 40% of the
# branch's own stress range -- a concrete number, not "some minimum", so a
# narrow window cannot quietly win the search by overfitting a handful of
# points.
_STIFF_MIN_POINTS = 10
_STIFF_R2_EPSILON = 0.001  # candidates within this much R2 of the best found
# are treated as tied; among ties, the WIDEST window wins (see
# _auto_stiffness_window) rather than raw argmax(R2), which is gameable by a
# window sitting right at the minimum-span edge beating a more representative
# wider one by noise alone.
_STIFF_GRID_STEPS = 21  # candidate lo/hi fractions of the branch's own
# stress range: 0.00, 0.05, ..., 1.00.


def _auto_stiffness_window(
    s_load: np.ndarray, x_load: np.ndarray, cfg: Config
) -> tuple[Optional[float], int, Optional[float], Optional[float], Optional[float]]:
    """Search the (already loading-branch-only) stress/displacement pair for
    its own best-fit linear region.

    Returns (slope, n, r2, lo_mpa, hi_mpa) -- the window bounds travel with
    the fit so the automatic choice is auditable, not a black box (every
    consumer that stores this reports the bounds alongside the number).

    Falls back to the FIXED `stiff_lo_frac`/`stiff_hi_frac` window -- exactly
    what this codebase used before this redesign -- when no candidate clears
    the minimum span/point bar (a real possibility on a short or noisy
    cycle). A visible "no stiffness reported" regression for that one cycle
    would be worse than reusing the old default.
    """
    if s_load.size == 0:
        return None, 0, None, None, None
    branch_peak = float(np.max(s_load))
    branch_min = float(np.min(s_load))
    span = branch_peak - branch_min
    if span <= 0:
        return None, 0, None, None, None

    fracs = np.linspace(0.0, 1.0, _STIFF_GRID_STEPS)
    candidates = []  # (r2, width, slope, n, lo_mpa, hi_mpa)
    for lo_f in fracs:
        for hi_f in fracs:
            if hi_f - lo_f < _STIFF_MIN_SPAN_FRAC:
                continue
            lo_mpa = branch_min + lo_f * span
            hi_mpa = branch_min + hi_f * span
            slope, n, r2 = _fit_window(s_load, x_load, lo_mpa, hi_mpa)
            if slope is None or r2 is None or n < _STIFF_MIN_POINTS:
                continue
            candidates.append((r2, hi_mpa - lo_mpa, slope, n, lo_mpa, hi_mpa))

    if not candidates:
        lo_mpa = cfg.stiff_lo_frac * branch_peak
        hi_mpa = cfg.stiff_hi_frac * branch_peak
        slope, n, r2 = _fit_window(s_load, x_load, lo_mpa, hi_mpa)
        return slope, n, r2, lo_mpa, hi_mpa

    best_r2 = max(c[0] for c in candidates)
    near_best = [c for c in candidates if c[0] >= best_r2 - _STIFF_R2_EPSILON]
    r2, _width, slope, n, lo_mpa, hi_mpa = max(near_best, key=lambda c: c[1])
    return slope, n, r2, lo_mpa, hi_mpa


def _energies(stress, disp) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Work in / recovered / dissipated, per unit CROSS-SECTIONAL AREA (MPa*mm).

    Integrating stress over displacement gives (1/A) * integral(F dx), which is
    work per unit area, NOT per unit volume. Divide by h0 for work per unit
    volume, which lands in MPa.

    The path is split at MAXIMUM DISPLACEMENT, not maximum stress. In a
    load-controlled test with a dwell, the specimen keeps creeping while
    stress is already held constant or falling, so displacement peaks AFTER
    stress does. Splitting at max stress puts part of the still-advancing
    deformation into the unloading integral, which can make the dissipated
    energy come out negative -- physically impossible.
    """
    turn = int(np.argmax(disp))
    if turn < 1 or turn >= len(disp) - 1:
        return None, None, None
    w_in = float(abs(np.trapezoid(stress[: turn + 1], disp[: turn + 1])))
    w_out = float(abs(np.trapezoid(stress[turn:], disp[turn:])))
    return w_in, w_out, w_in - w_out


def analyse_test(test: TestData, cfg: Optional[Config] = None) -> pd.DataFrame:
    """Per-cycle metric table for one specimen."""
    cfg = cfg or Config()
    s, x = test.stress_mpa, test.displacement_mm
    cycles = segment_cycles(s, cfg)
    if not cycles:
        return pd.DataFrame()

    peaks = [float(np.nanmax(s[a : b + 1])) for a, b in cycles]
    global_peak = max(peaks)

    # ref_peak picks which cycle the common-band stiffness window (below) is
    # auto-located on -- it is NOT a stress anything is measured at; the one
    # reference stress (residual_stress, see Config) is anchored to the
    # global peak instead and does not depend on this at all.
    #
    # Among cycles that pass segmentation, prefer one with a detected dwell:
    # a short, fast, hold-free excursion at the very start of a record is a
    # plausible preload/seating ramp (the machine settling full contact
    # before the programmed sequence proper), and adaptive segmentation can
    # legitimately surface one as its own cycle now that it is no longer
    # silently merged into whatever follows it (see segment_cycles) --
    # exactly the kind of previously-hidden real signal this redesign exists
    # to stop discarding. But anchoring the common-band window to something
    # smaller and unlike every real stage narrows it for every OTHER cycle
    # too, for no comparability benefit. Falls back to the plain smallest
    # peak if no cycle has a detected hold -- a genuinely fast-cycling test
    # (or one with detect_holds off) is not making a false claim either way.
    held_peaks = [
        peak for peak, (a, b) in zip(peaks, cycles)
        if detect_hold(s[a : b + 1], cfg) is not None
    ]
    ref_peak = min(held_peaks) if held_peaks else min(peaks)
    residual_stress = cfg.residual_stress_mpa or cfg.residual_stress_frac * global_peak

    # Common-band stiffness window: auto-located ONCE, on the reference
    # (smallest-peak) cycle's own loading branch, then reused as identical
    # ABSOLUTE stress bounds on every cycle -- preserving the "same window
    # every cycle, so cycles are comparable" property the common band exists
    # for, with the window itself found from data instead of a fixed guess
    # (see _auto_stiffness_window's docstring / ASTM E111 toe compensation).
    a_ref, b_ref = cycles[peaks.index(ref_peak)]
    s_ref, x_ref = s[a_ref : b_ref + 1], x[a_ref : b_ref + 1]
    ref_peak_idx = int(np.argmax(s_ref))
    _, _, _, common_lo, common_hi = _auto_stiffness_window(
        s_ref[: ref_peak_idx + 1], x_ref[: ref_peak_idx + 1], cfg
    )
    if common_lo is None:
        common_lo, common_hi = cfg.stiff_lo_frac * ref_peak, cfg.stiff_hi_frac * ref_peak

    rows = []
    for n, (a, b) in enumerate(cycles, start=1):
        cs, cx = s[a : b + 1], x[a : b + 1]
        peak = float(np.nanmax(cs))
        peak_idx = int(np.argmax(cs))

        hold = detect_hold(cs, cfg)
        if hold is not None:
            h_lo, h_hi = hold
            creep = float(cx[h_hi] - cx[h_lo])
            hold_pts = h_hi - h_lo + 1
        else:
            creep, hold_pts = None, 0

        w_in, w_out, w_diss = _energies(cs, cx)
        cs_load, cx_load = cs[: peak_idx + 1], cx[: peak_idx + 1]
        k_common, k_common_n, k_common_r2 = _fit_window(cs_load, cx_load, common_lo, common_hi)
        k_rel, k_rel_n, k_rel_r2, rel_lo, rel_hi = _auto_stiffness_window(cs_load, cx_load, cfg)

        rows.append(
            {
                "Cycle": n,
                "PeakStress_MPa": peak,
                # Displacement at the instant of MAXIMUM STRESS. Not the same
                # as the largest displacement in the cycle -- see MaxDisp_mm.
                "PeakDisp_mm": float(cx[peak_idx]),
                # Largest displacement reached in the cycle, and the point the
                # energy integrals split the loop at. It falls at or after the
                # stress peak because the specimen keeps creeping while stress
                # is held; on a long dwell it can exceed PeakDisp_mm by 20%+.
                # It is NOT necessarily at the end of the dwell -- see
                # StressAtMaxDisp_MPa below.
                "MaxDisp_mm": float(np.nanmax(cx)),
                # Stress at the instant of maximum displacement. On an intact
                # specimen this equals the peak: displacement stops growing
                # when the load stops being held. Once it falls BELOW the peak
                # the specimen went on compacting while the load was being
                # removed, which is a damage signature no other column here
                # carries. Reported in MPa so it can be read against the peak.
                "StressAtMaxDisp_MPa": float(cs[int(np.argmax(cx))]),
                # --- the one reference stress, both branches -----------------
                # Read on the loading branch at a LOW common stress, not at
                # zero: at zero stress the specimen loses contact and the
                # displacement signal returns to its unloaded baseline, which
                # makes a zero-referenced permanent set meaningless here. This
                # single value now does double duty: subtracted cycle-within-
                # cycle it is permanent deformation (PermDef_incremental_mm
                # below); plotted cycle over cycle it is the cross-cycle
                # comparison a separate mid-range reference used to serve
                # (dropped -- unreachable on exactly the small/single-cycle
                # tests this engine exists to still get right; see Config).
                "ResidualDisp_mm": _interp_on_branch(cs, cx, residual_stress, "loading"),
                # The SAME reference stress, read on the UNLOADING branch of
                # THIS cycle. The gap between this and ResidualDisp_mm above,
                # within one cycle, is the permanent set gained in that one
                # cycle -- see PermDef_incremental_mm below. Needs no earlier
                # or later cycle to exist, so it is defined identically for a
                # single-cycle test and cycle 7 of a 20-cycle test.
                "ResidualDisp_unload_mm": _interp_on_branch(cs, cx, residual_stress, "unloading"),
                # --- comparable stiffness -----------------------------------
                # Common band: identical stress window in every cycle and every
                # test => valid to compare across stages and materials. The
                # window itself is auto-located once, on the reference cycle's
                # own loading branch -- see analyse_test -- and reported here
                # so the automatic choice stays auditable.
                "Stiffness_common_MPa_per_mm": k_common,
                "Stiffness_common_n": k_common_n,
                "Stiffness_common_r2": k_common_r2,
                "Stiffness_common_lo_MPa": common_lo,
                "Stiffness_common_hi_MPa": common_hi,
                # Relative band: auto-located on THIS cycle's own loading
                # branch. Describes the cycle faithfully but is NOT comparable
                # across rising stages of a multi-stage test.
                "Stiffness_relative_MPa_per_mm": k_rel,
                "Stiffness_relative_n": k_rel_n,
                "Stiffness_relative_r2": k_rel_r2,
                "Stiffness_relative_lo_MPa": rel_lo,
                "Stiffness_relative_hi_MPa": rel_hi,
                # --- energy --------------------------------------------------
                "Energy_in_MPa_mm": w_in,
                "Energy_dissipated_MPa_mm": w_diss,
                # Relative loss is the cross-test comparable form: absolute loss
                # scales with stress amplitude, so a 50 MPa and a 450 MPa stage
                # cannot be compared on the absolute number.
                "HysteresisLoss_rel": (w_diss / w_in) if (w_in and w_in > 0) else None,
                # --- creep ---------------------------------------------------
                "HoldDetected": hold is not None,
                "HoldPoints": hold_pts,
                "Creep_during_hold_mm": creep,
                "_start": a,
                "_end": b,
            }
        )

    df = pd.DataFrame(rows)

    # Permanent deformation -- WITHIN-CYCLE before/after, not referenced to
    # any other cycle. This is what makes it well-defined at every cycle
    # count, including exactly 1: PermDef_incremental_mm is simply how much
    # residual displacement THIS cycle gained between being read on the way
    # up (ResidualDisp_mm) and read again on the way back down
    # (ResidualDisp_unload_mm), at the identical reference stress both
    # times. The OLD formula compared cycle N's loading-branch reading
    # against CYCLE 1's loading-branch reading -- structurally meaningless
    # for a single-cycle test (it compares cycle 1 to itself, always 0,
    # regardless of how much permanent set actually occurred) and silent
    # about which cycle it landed on if cycle 1's own reading was NaN.
    res_load = pd.to_numeric(df["ResidualDisp_mm"], errors="coerce")
    res_unload = pd.to_numeric(df["ResidualDisp_unload_mm"], errors="coerce")
    df["ResidualDisp_mm"] = res_load
    df["ResidualDisp_unload_mm"] = res_unload
    df["PermDef_incremental_mm"] = res_unload - res_load
    # Cumulative is a running total of the (redefined) incremental values --
    # still the drift signal for a multi-cycle test, but built bottom-up from
    # N independently-meaningful measurements instead of one long-baseline
    # comparison that goes vacuous at N=1. pandas' cumsum skips NaN by
    # default: a cycle whose own incremental figure is unreadable reports NaN
    # for ITS OWN cumulative total too (nothing else to report), but every
    # cycle after it resumes from the last valid running total rather than
    # going permanently NaN from that point on.
    df["PermDef_cumulative_mm"] = df["PermDef_incremental_mm"].cumsum()

    if test.h0_mm:
        for src, dst in [
            ("PermDef_cumulative_mm", "PermDef_cumulative_pct"),
            ("PermDef_incremental_mm", "PermDef_incremental_pct"),
            ("PeakDisp_mm", "PeakStrain_pct"),
            ("MaxDisp_mm", "MaxStrain_pct"),
            ("Creep_during_hold_mm", "Creep_pct"),
        ]:
            df[dst] = df[src] / test.h0_mm * 100.0

    df.attrs["label"] = test.label
    df.attrs["residual_stress_mpa"] = residual_stress
    df.attrs["global_peak_mpa"] = global_peak
    # The auto-located common-band window is ONE pair of bounds for the whole
    # test (found once, on the reference cycle -- see above), not a per-cycle
    # value, so it travels as a test-level attribute rather than a column.
    df.attrs["stiffness_common_lo_mpa"] = common_lo
    df.attrs["stiffness_common_hi_mpa"] = common_hi
    df.attrs["multi_stage"] = bool(np.ptp(peaks) > 0.05 * global_peak)
    df.attrs["h0_mm"] = test.h0_mm
    df.attrs["notes"] = list(test.notes)
    return df
