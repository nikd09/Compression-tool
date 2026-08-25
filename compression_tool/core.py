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

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------


@dataclass
class Config:
    """All tuning knobs. Defaults are relative and should work unmodified."""

    # --- segmentation -------------------------------------------------------
    unload_frac: float = 0.02      # stress below this * peak => specimen unloaded
    major_cycle_frac: float = 0.10  # cycle peak below this * global peak => noise
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
    stiff_lo_frac: float = 0.25    # lower bound of regression window
    stiff_hi_frac: float = 0.75    # upper bound of regression window

    # --- reference stress for cross-cycle comparison ------------------------
    # None => auto: use the SMALLEST cycle peak in the test, so the reference
    # level is reachable in every cycle of a multi-stage test.
    ref_stress_mpa: Optional[float] = None
    ref_stress_frac: float = 0.50  # used with the auto reference peak

    # low reference stress for residual/permanent-set readout.
    # Measured on the loading branch, NOT at zero stress -- see notes below.
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


def load_series_format(path: str, cfg: Config) -> list[TestData]:
    """Multi-sample workbook: 'Werte Serie' holds sample columns side by side.

    Layout (row 0 = sample no, row 1 = channel name, row 2 = unit, then data).
    Column pairs are (displacement, stress) per sample -- but which of the two
    is which is decided from the UNIT row, never from position.
    """
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheets = xl.sheet_names
    values_sheet = _find_sheet(sheets, _SERIES_VALUES_RE)
    if values_sheet is None:
        raise ValueError("No 'Werte Serie' sheet found")

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
                label=f"{_stem(path)}_S{sid}",
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


def load_single_format(path: str, cfg: Config) -> list[TestData]:
    """One sample per sheet: row 0 = title, row 1 = channel, row 2 = unit.

    Such exports often carry BOTH a crosshead channel (Standardweg) and an
    extensometer channel (Sonder LAA). The extensometer is preferred because
    the crosshead signal includes machine compliance.
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
            label=_stem(path),
            displacement_mm=disp,
            stress_mpa=stress,
            source_file=path,
            source_format="single",
            displacement_channel=channels[d_col],
            h0_mm=cfg.h0_mm,
            notes=notes,
        )
    ]


def load_tests(path: str, cfg: Optional[Config] = None) -> list[TestData]:
    """Entry point: detect the export layout and return one TestData per specimen."""
    cfg = cfg or Config()
    fmt = detect_format(path)
    return load_series_format(path, cfg) if fmt == "series" else load_single_format(path, cfg)


def _stem(path: str) -> str:
    import os

    return os.path.splitext(os.path.basename(path))[0]


# ----------------------------------------------------------------------------
# Segmentation
# ----------------------------------------------------------------------------


def segment_cycles(stress: np.ndarray, cfg: Config) -> list[tuple[int, int]]:
    """Split into load-unload cycles using RELATIVE thresholds.

    A cycle is a contiguous run where the specimen is loaded, bounded by runs
    where stress falls back to the unloaded floor. Works unchanged whether the
    test peaks at 2 MPa or 450 MPa, and whether peaks are constant or rising.
    """
    if len(stress) == 0:
        # np.nanmax raises on a zero-size array rather than returning NaN --
        # this is the one shape an all-NaN array (handled below, via the
        # "no cycle cleared the floor" path) does not cover. A malformed or
        # completely unparseable column reaches here as a zero-length array,
        # and "no cycles" is exactly the correct answer for it too.
        return []
    peak = float(np.nanmax(stress))
    if peak <= 0:
        return []

    loaded = stress > cfg.unload_frac * peak
    cycles: list[tuple[int, int]] = []
    start = None
    for i, on in enumerate(loaded):
        if on and start is None:
            start = i
        elif not on and start is not None:
            cycles.append((start, i - 1))
            start = None
    if start is not None:
        cycles.append((start, len(stress) - 1))

    kept = []
    for s_i, e_i in cycles:
        if e_i - s_i + 1 < cfg.min_cycle_points:
            continue
        if float(np.nanmax(stress[s_i : e_i + 1])) < cfg.major_cycle_frac * peak:
            continue
        kept.append((s_i, e_i))
    if not kept:
        return []

    # Expand each run outward into the adjacent unloaded valleys, down to the
    # local stress minimum. Without this the cycle would begin ABOVE the
    # detection threshold, so the loading branch could never be interpolated
    # at any reference stress below that threshold.
    out = []
    for k, (s_i, e_i) in enumerate(kept):
        prev_end = kept[k - 1][1] if k > 0 else 0
        next_start = kept[k + 1][0] if k + 1 < len(kept) else len(stress) - 1
        lo = prev_end + int(np.argmin(stress[prev_end : s_i + 1])) if s_i > prev_end else s_i
        hi = e_i + int(np.argmin(stress[e_i : next_start + 1])) if next_start > e_i else e_i
        out.append((lo, hi))
    return out


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


def _interp_on_branch(
    stress: np.ndarray, disp: np.ndarray, target: float, branch: str
) -> Optional[float]:
    """Displacement where the branch crosses `target` stress (linear interp)."""
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
        if hit:
            if b == a:
                return float(seg_x[i])
            t = (target - a) / (b - a)
            return float(seg_x[i - 1] + t * (seg_x[i] - seg_x[i - 1]))
    return None


# ----------------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------------


def _stiffness(stress, disp, lo_mpa, hi_mpa) -> tuple[Optional[float], int, Optional[float]]:
    """Slope d(stress)/d(disp) on the loading branch, MPa/mm.

    Returns (slope, n_points, r_squared). The last two are quality flags: a
    slope fitted to a handful of points on a fast machine ramp is not
    trustworthy, and the UI must be able to say so rather than plot it as if
    it were solid.
    """
    peak_idx = int(np.argmax(stress))
    s_load, x_load = stress[: peak_idx + 1], disp[: peak_idx + 1]
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

    # Reference stress must be reachable in EVERY cycle, so it is tied to the
    # smallest cycle peak. In a multi-stage test (50 -> 450 MPa) a reference
    # based on the global peak would be unreachable in the early cycles.
    ref_peak = min(peaks)
    ref_stress = cfg.ref_stress_mpa or cfg.ref_stress_frac * ref_peak
    stiff_lo, stiff_hi = cfg.stiff_lo_frac * ref_peak, cfg.stiff_hi_frac * ref_peak
    residual_stress = cfg.residual_stress_mpa or cfg.residual_stress_frac * global_peak

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
        k_common, k_common_n, k_common_r2 = _stiffness(cs, cx, stiff_lo, stiff_hi)
        k_rel, k_rel_n, k_rel_r2 = _stiffness(
            cs, cx, cfg.stiff_lo_frac * peak, cfg.stiff_hi_frac * peak
        )

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
                # --- residual / permanent deformation -----------------------
                # Read on the loading branch at a LOW common stress, not at
                # zero: at zero stress the specimen loses contact and the
                # displacement signal returns to its unloaded baseline, which
                # makes a zero-referenced permanent set meaningless here.
                "ResidualDisp_mm": _interp_on_branch(cs, cx, residual_stress, "loading"),
                # --- comparable stiffness -----------------------------------
                # Common band: identical stress window in every cycle and every
                # test => valid to compare across stages and materials.
                "Stiffness_common_MPa_per_mm": k_common,
                "Stiffness_common_n": k_common_n,
                "Stiffness_common_r2": k_common_r2,
                # Relative band: 25-75% of THIS cycle's own peak. Describes the
                # cycle faithfully but is NOT comparable across rising stages.
                "Stiffness_relative_MPa_per_mm": k_rel,
                "Stiffness_relative_n": k_rel_n,
                "Stiffness_relative_r2": k_rel_r2,
                # --- displacement at reference stress, both branches ---------
                "DispAtRef_load_mm": _interp_on_branch(cs, cx, ref_stress, "loading"),
                "DispAtRef_unload_mm": _interp_on_branch(cs, cx, ref_stress, "unloading"),
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

    # Permanent deformation, referenced to cycle 1 (cumulative) and to the
    # preceding cycle (incremental).
    res = pd.to_numeric(df["ResidualDisp_mm"], errors="coerce")
    df["ResidualDisp_mm"] = res
    df["PermDef_cumulative_mm"] = (res - res.dropna().iloc[0]) if res.notna().any() else np.nan
    df["PermDef_incremental_mm"] = res.diff()

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
    df.attrs["ref_stress_mpa"] = ref_stress
    df.attrs["residual_stress_mpa"] = residual_stress
    df.attrs["global_peak_mpa"] = global_peak
    df.attrs["multi_stage"] = bool(np.ptp(peaks) > 0.05 * global_peak)
    df.attrs["h0_mm"] = test.h0_mm
    df.attrs["notes"] = list(test.notes)
    return df
