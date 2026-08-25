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

from dataclasses import asdict, dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .core import Config, TestData, segment_cycles

# Severity is about what the reader must do, not how bad it looks.
#   critical -- a number may be wrong; do not quote it until resolved
#   caution  -- the number is right but is easily misread
#   info     -- worth knowing, no action
SEVERITIES = ("critical", "caution", "info")

# Warn when the smallest cycle peak sits within this fraction of the discard
# threshold. At 0.75 a test whose first stage clears the threshold by less than
# a third of its own height gets flagged.
FIRST_CYCLE_MARGIN = 0.75

# Hold lengths differing by more than this ratio make hold displacement
# non-comparable across cycles without normalising.
DWELL_RATIO_TOLERANCE = 1.10


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
    """The first cycle is the reference for every cumulative figure.

    `major_cycle_frac` discards any cycle whose own peak falls below that
    fraction of the global peak. In a rising multi-stage test the FIRST stage
    is by definition the smallest, so it is always the one closest to being
    discarded -- and losing it silently rebases every cumulative permanent
    deformation in the table onto what used to be cycle 2.
    """
    if df.empty:
        return None
    peaks = pd.to_numeric(df["PeakStress_MPa"], errors="coerce").dropna()
    if peaks.empty:
        return None

    global_peak = float(np.nanmax(stress))
    smallest = float(peaks.min())
    if global_peak <= 0:
        return None

    # The fraction at which the smallest cycle would start being discarded.
    critical_frac = smallest / global_peak
    if critical_frac <= 0:
        return None
    proximity = cfg.major_cycle_frac / critical_frac
    if proximity < FIRST_CYCLE_MARGIN:
        return None

    return Warning_(
        code="first_cycle_near_discard_threshold",
        severity="critical" if proximity >= 0.95 else "caution",
        message=(
            f"The smallest cycle peaks at {smallest:.1f} MPa against a discard "
            f"threshold of {cfg.major_cycle_frac * global_peak:.1f} MPa "
            f"(major_cycle_frac = {cfg.major_cycle_frac:g}), clearing it by only "
            f"{(critical_frac / cfg.major_cycle_frac - 1) * 100:.0f}%. Any "
            f"major_cycle_frac above {critical_frac:.3f} discards that cycle. "
            "Because cycle 1 is the reference for every cumulative permanent "
            "deformation, losing it rebases the whole column without any other "
            "visible change."
        ),
    )


def _first_cycle_residual_unreachable(df: pd.DataFrame) -> Optional[Warning_]:
    """PermDef_cumulative_mm is referenced to the first cycle whose residual
    displacement could actually be READ on the loading branch -- which is not
    necessarily cycle 1. The residual reference stress is a fraction of the
    GLOBAL peak (core.py), so in a rising multi-stage test cycle 1's own,
    much smaller peak can sit below it entirely; `_interp_on_branch` then
    returns None for cycle 1 and the "- res.dropna().iloc[0]" rebase silently
    anchors onto whichever cycle DOES reach it instead. Distinct from
    `_first_cycle_at_risk`, which covers cycle 1 being discarded outright --
    this covers cycle 1 surviving segmentation but still not producing a
    residual reading.
    """
    if df.empty or "ResidualDisp_mm" not in df.columns:
        return None
    res = pd.to_numeric(df["ResidualDisp_mm"], errors="coerce")
    if res.empty or pd.notna(res.iloc[0]) or not res.notna().any():
        return None

    first_valid_idx = res.notna().idxmax()
    rebase_cycle = df.loc[first_valid_idx, "Cycle"] if "Cycle" in df.columns else "a later cycle"
    return Warning_(
        code="first_cycle_residual_unreachable",
        severity="critical",
        message=(
            "Cycle 1's loading branch never reached the residual reference "
            "stress, so PermDef_cumulative_mm is silently referenced to "
            f"cycle {rebase_cycle} instead of cycle 1 -- every value in that "
            "column is relative to the wrong baseline. Raise "
            "residual_stress_frac's reach by lowering it, or note that this "
            "material's early stage(s) cannot report a permanent-set figure."
        ),
    )


def _cycles_discarded(stress: np.ndarray, cfg: Config) -> Optional[Warning_]:
    """Which long-enough runs the peak filter actually threw away.

    Identified by re-applying the filter's own criterion rather than by diffing
    against the kept cycles: dropping a cycle changes how its neighbours expand
    into the surrounding valleys, so start indices do not line up between the
    two segmentations and matching on them mis-attributes the discard.

    Usually the casualty is the machine's contact-finding approach at the start
    of a record, which SHOULD go. Reporting the peaks lets that be confirmed at
    a glance rather than taken on faith.
    """
    global_peak = float(np.nanmax(stress))
    if global_peak <= 0:
        return None

    permissive = segment_cycles(stress, Config(**{**vars(cfg), "major_cycle_frac": 0.0}))
    cutoff = cfg.major_cycle_frac * global_peak
    dropped = [
        (a, float(np.nanmax(stress[a : b + 1])))
        for a, b in permissive
        if float(np.nanmax(stress[a : b + 1])) < cutoff
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
            f"{len(dropped)} load run(s) long enough to be a cycle were discarded "
            f"for peaking below {cfg.major_cycle_frac:g} x the global peak: {detail}. "
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

    found = [
        _gauge_length(test, has_strain, gauge_length_confirmed),
        _first_cycle_at_risk(stress, df, cfg),
        _first_cycle_residual_unreachable(df),
        _cycles_discarded(stress, cfg),
        _variable_dwell(df),
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
