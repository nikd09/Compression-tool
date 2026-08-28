"""
diagnostics.py
==============
Conditions that change how a result should be read.

These are not errors -- every one of them describes a run that completed and
produced numbers. They exist because the numbers alone do not carry their own
caveats: a cycle count is silent about the cycle that was nearly discarded, and
a strain column looks identical whether or not anyone has confirmed the gauge
length it was divided by.

Each warning travels in the JSON record, so a result carries its own reading
instructions wherever it goes.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .core import Config, TestData, segment_cycles

# Severity is about what the reader must do, not how bad it looks.
#   critical -- a number may be wrong; do not quote it until resolved
#   caution  -- the number is right but is easily misread
#   info     -- worth knowing, no action
SEVERITIES = ("critical", "caution", "info")

# _first_cycle_at_risk tightens unload_frac by this factor when probing
# whether the reference cycle survives on a slightly stricter setting. At
# 0.75, unload_frac has to clear its own margin by at least 25% (in
# candidate/valley-depth ratio terms -- see core.segment_cycles) to be
# judged comfortable rather than fragile.
FIRST_CYCLE_MARGIN = 0.75

# Hold lengths differing by more than this ratio make hold displacement
# non-comparable across cycles without normalising.
DWELL_RATIO_TOLERANCE = 1.10

# residual_stress is documented (core.py, ResidualDisp_mm) as a LOW
# reference stress. A cycle whose own peak is small enough that the
# (test-wide) residual reference stress reaches this fraction of it is
# exactly the case where "low relative to this cycle" stops being true --
# most exposed on a small or single-cycle test, which is what this
# diagnostic exists to catch.
RESIDUAL_HIGH_FRAC = 0.5


@dataclass(frozen=True)
class Warning_:
    code: str
    severity: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


def _first_cycle_at_risk(
    stress: np.ndarray, df: pd.DataFrame, cfg: Config
) -> Optional[Warning_]:
    """The reference cycle (smallest peak -- used for ref_stress and the
    common-band stiffness window) is close to disappearing.

    Segmentation no longer accepts a cycle by comparing its peak to one fixed
    fraction of the global peak -- it accepts a candidate when its bounding
    valley clears an adaptive local-noise floor AND gives back at least
    `unload_frac` of the candidate's own height (see `core.segment_cycles`).
    There is no longer a single number to compare the reference cycle's peak
    against directly, so "close to being discarded" is measured the way the
    engine itself would answer it: by re-running segmentation with
    `unload_frac` tightened by a safety margin and checking whether the SAME
    cycle still comes out the other side.

    This also fixes a real blind spot in the old version: built around a
    smallest-peak-vs-global-peak ratio, it was a FIXED, uninformative
    constant at exactly one cycle (smallest and global peak are the same
    value there, always) rather than reflecting the actual signal -- so it
    could never fire for a single-cycle test regardless of how fragile
    segmentation actually was. The rewrite reflects real risk at any cycle
    count, including 1, whenever a genuine competing candidate is nearby (a
    near-miss merge can leave a test at 1 cycle just as easily as at 2). A
    signal with only one true local maximum and no competing candidate at
    all has nothing to be fragile RELATIVE TO -- its own peak trivially
    equals the global peak and there is no neighbouring valley to fail the
    ratio test against -- so it correctly stays quiet rather than
    manufacturing a warning to compensate for the old blind spot.
    """
    if df.empty:
        return None
    peaks = pd.to_numeric(df["PeakStress_MPa"], errors="coerce").dropna()
    if peaks.empty:
        return None
    ref_peak = float(peaks.min())

    tighter = replace(cfg, unload_frac=min(cfg.unload_frac / FIRST_CYCLE_MARGIN, 0.999))
    probe = segment_cycles(stress, tighter)
    probe_peaks = [float(np.nanmax(stress[a : b + 1])) for a, b in probe]
    if any(np.isclose(p, ref_peak, rtol=1e-6) for p in probe_peaks):
        return None  # survives a margin-tightened floor -- comfortable

    return Warning_(
        code="first_cycle_near_discard_threshold",
        severity="critical",
        message=(
            f"The reference cycle (smallest peak, {ref_peak:.2f} MPa -- used for "
            "the cross-cycle reference stress and the common-band stiffness "
            f"window) is no longer found when unload_frac is tightened by "
            f"{(1 - FIRST_CYCLE_MARGIN) * 100:.0f}% ({cfg.unload_frac:g} -> "
            f"{tighter.unload_frac:g}). It is close to the margin segmentation "
            "actually accepted it by; a small change in the raw signal or the "
            "config could merge or drop it."
        ),
    )


def _residual_unreadable_cycles(df: pd.DataFrame) -> Optional[Warning_]:
    """Within-cycle permanent deformation (PermDef_incremental_mm) needs BOTH
    ResidualDisp_mm (loading branch) and ResidualDisp_unload_mm (unloading
    branch) to be readable for that specific cycle.

    Unlike the old cross-cycle formula, an unreadable cycle no longer
    silently rebases every OTHER cycle onto a different baseline -- it
    reports NaN for itself alone, at every cycle count including 1. This
    names which cycle(s) that happened to, rather than leaving a bare NaN
    with no explanation in the table.
    """
    if df.empty or "ResidualDisp_mm" not in df.columns:
        return None
    load = pd.to_numeric(df["ResidualDisp_mm"], errors="coerce")
    unload = pd.to_numeric(df.get("ResidualDisp_unload_mm"), errors="coerce")
    missing = df.loc[load.isna() | unload.isna(), "Cycle"].tolist()
    if not missing:
        return None

    which = ", ".join(str(c) for c in missing)
    return Warning_(
        code="residual_unreadable_cycles",
        severity="critical",
        message=(
            f"Cycle(s) {which} could not read the residual reference stress on "
            "the loading and/or unloading branch, so PermDef_incremental_mm (and "
            "PermDef_cumulative_mm from that point on) is NaN for that cycle. "
            "Usually means the cycle's own peak is too close to the residual "
            "reference stress -- lower residual_stress_frac, or note that this "
            "cycle cannot report a permanent-set figure."
        ),
    )


def _residual_reference_not_low(
    df: pd.DataFrame, residual_stress: Optional[float]
) -> Optional[Warning_]:
    """ResidualDisp_mm is documented as read at a LOW reference stress -- the
    permanent-set reading depends on that being true. On a cycle whose own
    peak is small enough that the (test-wide) residual reference stress
    reaches RESIDUAL_HIGH_FRAC of it, "low" no longer describes that cycle:
    the reading sits close enough to the top of what that cycle ever
    reached that treating it as a near-baseline reference is questionable.
    Most exposed on a small or single-cycle test, which is exactly the case
    this whole engine redesign is meant to still get right.
    """
    if df.empty or not residual_stress:
        return None
    peaks = pd.to_numeric(df["PeakStress_MPa"], errors="coerce")
    at_risk = df.loc[
        (peaks > 0) & (residual_stress >= RESIDUAL_HIGH_FRAC * peaks), "Cycle"
    ].tolist()
    if not at_risk:
        return None

    which = ", ".join(str(c) for c in at_risk)
    return Warning_(
        code="residual_reference_not_low",
        severity="caution",
        message=(
            f"Cycle(s) {which}: the residual reference stress "
            f"({residual_stress:.2f} MPa) reaches {RESIDUAL_HIGH_FRAC:g}x or more "
            "of that cycle's own peak stress, so it is no longer a LOW reference "
            "for that cycle specifically -- the assumption ResidualDisp_mm's "
            "reading depends on. Treat that cycle's permanent-deformation figures "
            "as less certain than the others."
        ),
    )


def _cycles_discarded(test: TestData, cfg: Config) -> Optional[Warning_]:
    """Which candidate load runs major_cycle_frac's absolute floor actually
    threw away, on top of what the local-noise and unload_frac ratio checks
    alone would have kept.

    Identified by re-running segmentation with ONLY major_cycle_frac zeroed
    -- unload_frac stays at its real, configured value, since it answers a
    different question ("did this candidate genuinely separate from its own
    neighbour") that stays relevant even at a permissive peak-height floor;
    zeroing it too let single-sample-scale artefacts at the test's true
    unloaded baseline (near enough to 0 MPa that they clear almost any ratio
    trivially) flood this warning with noise that was never a real
    candidate stage. Any resulting cycle whose peak does not appear among
    the real, fully-floored result is reported -- rather than diffing index
    ranges, which shift between the two runs as neighbours expand
    differently once a run is dropped.

    Usually the casualty is the machine's contact-finding approach at the
    start of a record, which SHOULD go. Reporting the peaks lets that be
    confirmed at a glance rather than taken on faith.
    """
    stress = test.stress_mpa
    if len(stress) == 0:
        return None
    global_peak = float(np.nanmax(stress))
    if global_peak <= 0:
        return None

    real = segment_cycles(stress, cfg)
    real_peaks = [float(np.nanmax(stress[a : b + 1])) for a, b in real]
    permissive = segment_cycles(stress, replace(cfg, major_cycle_frac=0.0))
    dropped = [
        (a, float(np.nanmax(stress[a : b + 1])))
        for a, b in permissive
        if not any(np.isclose(float(np.nanmax(stress[a : b + 1])), rp, rtol=1e-6) for rp in real_peaks)
    ]
    if not dropped:
        return None

    detail = ", ".join(
        f"{peak:.1f} MPa ({peak / global_peak * 100:.1f}% of peak) at sample {a}"
        for a, peak in dropped
    )
    return Warning_(
        code="cycles_discarded_by_peak_filter",
        severity="caution",
        message=(
            f"{len(dropped)} load run(s) that separated cleanly from their own "
            f"neighbours were discarded by major_cycle_frac ({cfg.major_cycle_frac:g} "
            "x the global peak), now a safety floor rather than the primary "
            f"segmentation gate -- see Config: {detail}. "
            "A low-stress run at the start of the record is normally the machine "
            "finding contact and is correctly excluded; anything else may be a "
            "stage you are losing."
        ),
    )


def _variable_dwell(df: pd.DataFrame) -> Optional[Warning_]:
    """Unequal dwell lengths make hold displacement non-comparable."""
    if df.empty or "HoldPoints" not in df:
        return None
    held = pd.to_numeric(df["HoldPoints"], errors="coerce")
    held = held[held > 0]
    if len(held) < 2:
        return None
    lo, hi = float(held.min()), float(held.max())
    if lo <= 0 or hi / lo <= DWELL_RATIO_TOLERANCE:
        return None
    return Warning_(
        code="variable_dwell_length",
        severity="caution",
        message=(
            f"Dwell length varies from {lo:.0f} to {hi:.0f} samples ({hi / lo:.1f}x) "
            "across the cycles, so hold displacement is NOT comparable between "
            "them as a raw total -- a longer dwell accumulates more at identical "
            "material behaviour. Compare the per-1000-samples column instead, "
            "and note that it is a normalisation, not a creep rate."
        ),
    )


def _possible_preload_cycle(df: pd.DataFrame, cfg: Config) -> Optional[Warning_]:
    """A cycle with no detected dwell, in a test where dwell is clearly the
    norm, is most plausibly the machine's preload/seating step (settling
    full contact before the programmed sequence proper) rather than one of
    the real stages -- the same signature `analyse_test` already uses
    internally to prefer a HELD cycle as the cross-cycle reference (see its
    docstring). Segmentation can legitimately surface a genuine, cleanly
    separated load-unload event like this as its own cycle now that it is
    no longer silently merged into whatever follows it -- exactly the kind
    of previously-hidden real signal this engine's redesign exists to stop
    discarding (see core.segment_cycles). It is NOT excluded from the data
    or the cycle count here: it is real, non-noise signal, and quietly
    dropping it would trade one silent behaviour for another. This only
    makes the ambiguity visible, so a report is never read as if every
    counted cycle were a programmed stage without someone having checked.
    """
    if df.empty or not cfg.detect_holds or "HoldDetected" not in df:
        return None
    held = df["HoldDetected"].astype(bool)
    if held.all() or not held.any():
        return None
    # Only worth flagging when a dwell is clearly this test's norm -- a
    # genuinely mixed-dwell test (a legitimate design, not this signature)
    # is not what this diagnostic is for.
    if held.mean() <= 0.5:
        return None

    which = ", ".join(str(c) for c in df.loc[~held, "Cycle"].tolist())
    return Warning_(
        code="possible_preload_cycle",
        severity="caution",
        message=(
            f"Cycle(s) {which} have no detected dwell while {int(held.sum())} of "
            f"{len(df)} cycles in this test do. A clean, fully-separated "
            "load-unload event with no programmed hold, in an otherwise-held "
            "test, is most plausibly the machine's preload/seating step rather "
            "than a real programmed stage -- kept in the data because it is "
            "real signal, not noise, but excluded from this test's cross-"
            "cycle reference cycle for the same reason (see ref_stress_mpa / "
            "stiffness_common_lo_mpa). Confirm against the test protocol "
            "before counting it as a numbered stage in a report."
        ),
    )


def _gauge_length(test: TestData, has_strain: bool, confirmed: bool) -> Optional[Warning_]:
    """Strain and modulus are only as good as the gauge length behind them."""
    if not has_strain:
        return Warning_(
            code="no_gauge_length",
            severity="info",
            message=(
                "No specimen height h0 was available, so strain-normalised "
                "columns are omitted rather than estimated. Supply h0 to enable "
                "them."
            ),
        )
    if confirmed:
        return None
    channel = test.displacement_channel or "the displacement channel"
    return Warning_(
        code="gauge_length_unconfirmed",
        severity="critical",
        message=(
            f"Strain and any derived modulus are PROVISIONAL: it has not been "
            f"confirmed that '{channel}' spans only the {test.h0_mm:g} mm "
            "specimen. This is NOT the same question as which h0 to divide "
            "by -- a modulus-plausibility check can rule out a wrong h0 "
            "candidate (e.g. a crosshead reference length that implies an "
            "impossible modulus), but it cannot confirm what the "
            "extensometer physically spans; a channel that bridges extra "
            "material would still produce a plausible-looking modulus, just "
            "a wrong one. If it bridges additional material, every strain "
            "percentage and every modulus is wrong by that ratio, while all "
            "stress-based metrics remain correct. Confirm the fixturing with "
            "the person who ran the test, then re-ingest with "
            "gauge_length_confirmed=True."
        ),
    )


def collect(
    test: TestData,
    df: pd.DataFrame,
    cfg: Config,
    *,
    gauge_length_confirmed: bool = False,
) -> list[dict]:
    """Every warning that applies to one analysed specimen, worst first."""
    stress = test.stress_mpa
    has_strain = bool(test.h0_mm)

    residual_stress = df.attrs.get("residual_stress_mpa") if not df.empty else None
    found = [
        _gauge_length(test, has_strain, gauge_length_confirmed),
        _first_cycle_at_risk(stress, df, cfg),
        _residual_unreadable_cycles(df),
        _residual_reference_not_low(df, residual_stress),
        _cycles_discarded(test, cfg),
        _variable_dwell(df),
        _possible_preload_cycle(df, cfg),
    ]
    order = {name: i for i, name in enumerate(SEVERITIES)}
    return [
        w.as_dict()
        for w in sorted((w for w in found if w), key=lambda w: order[w.severity])
    ]


def distinct(payloads: Iterable[dict]) -> list[dict]:
    """Warnings across one or more specimen records, deduped by code, worst
    first.

    Specimens ingested under the same config typically trip the same
    warnings -- two runs of the same series export both fail the same
    gauge-length check, for instance. Showing the same paragraph once per
    specimen column is noise, not information, so every consumer (the
    workbook summary, the HTML report, the CLI) reduces through this one
    function rather than each keeping its own copy of the same dedup logic.
    """
    seen: dict[str, dict] = {}
    for payload in payloads:
        for w in payload.get("analysis", {}).get("warnings", []) or []:
            seen.setdefault(w.get("code", ""), w)
    order = {name: i for i, name in enumerate(SEVERITIES)}
    return sorted(seen.values(), key=lambda w: order.get(w.get("severity"), len(SEVERITIES)))


def strain_basis(test: TestData, *, gauge_length_confirmed: bool = False) -> dict:
    """What the strain columns were divided by, and whether anyone has checked.

    Recorded per specimen so a stored result can never be read as validated
    strain when nobody confirmed the gauge length that produced it.
    """
    return {
        "h0_mm": float(test.h0_mm) if test.h0_mm else None,
        "displacement_channel": test.displacement_channel or None,
        "gauge_length_confirmed": bool(gauge_length_confirmed),
        "strain_valid": bool(test.h0_mm) and bool(gauge_length_confirmed),
    }
