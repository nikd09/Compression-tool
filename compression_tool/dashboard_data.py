"""
dashboard_data.py
==================
Turns one or two specimen records (plus their curve caches) into the exact
shape the results dashboard template renders from.

Pure and Streamlit-free on purpose: the mapping from schema.py's column names
to the template's short JS keys is the part most likely to drift as either
side changes, so it lives in one tested place rather than inline in a view.
"""

from __future__ import annotations

from typing import Optional

from .schema import hold_disp_per_1000_samples, unload_yield_frac

# The template's colour palette and "Avg" series are built for exactly this
# case -- two specimens shown as S1 / S2 plus their mean, matching the old
# tool's grouped-bar idiom. A third specimen would reuse the Avg swatch.
MAX_SPECIMENS = 2


def _cycle_row(c: dict, points: list[list[float]]) -> dict:
    return {
        "n": c["Cycle"],
        "pts": points,
        "peakStress": c.get("PeakStress_MPa"),
        "peakDisp": c.get("PeakDisp_mm"),
        "maxDisp": c.get("MaxDisp_mm"),
        "stressAtMaxDisp": c.get("StressAtMaxDisp_MPa"),
        "unloadYield": unload_yield_frac(c.get("StressAtMaxDisp_MPa"), c.get("PeakStress_MPa")),
        "residDisp": c.get("ResidualDisp_mm"),
        "permCum": c.get("PermDef_cumulative_mm"),
        "permCumPct": c.get("PermDef_cumulative_pct"),
        "kCommon": c.get("Stiffness_common_MPa_per_mm"),
        "kCommonN": c.get("Stiffness_common_n"),
        "kCommonR2": c.get("Stiffness_common_r2"),
        "kRel": c.get("Stiffness_relative_MPa_per_mm"),
        "eIn": c.get("Energy_in_MPa_mm"),
        "eDiss": c.get("Energy_dissipated_MPa_mm"),
        "loss": c.get("HysteresisLoss_rel"),
        "holdPts": c.get("HoldPoints"),
        "holdDisp": c.get("Creep_during_hold_mm"),
        "holdPer1k": hold_disp_per_1000_samples(c.get("Creep_during_hold_mm"), c.get("HoldPoints")),
        "maxStrainPct": c.get("MaxStrain_pct"),
        "dispRefLoad": c.get("DispAtRef_load_mm"),
        "dispRefUnload": c.get("DispAtRef_unload_mm"),
    }


def _specimen_block(payload: dict, curve: Optional[dict], short: str) -> dict:
    spec, analysis, cfg = payload["specimen"], payload["analysis"], payload["config"]
    cycles = payload.get("cycles", [])

    peaks = [c["PeakStress_MPa"] for c in cycles if c.get("PeakStress_MPa") is not None]
    ref_peak = min(peaks) if peaks else None
    stiff_lo = round(cfg["stiff_lo_frac"] * ref_peak, 2) if ref_peak is not None else None
    stiff_hi = round(cfg["stiff_hi_frac"] * ref_peak, 2) if ref_peak is not None else None

    points_by_cycle = {c["cycle"]: c["points"] for c in (curve or {}).get("cycles", [])}

    return {
        "label": spec.get("label"),
        "short": short,
        "h0": spec.get("h0_mm"),
        "d0": spec.get("d0_mm"),
        "temp": spec.get("temperature_c"),
        "channel": spec.get("displacement_channel"),
        "refStress": analysis.get("ref_stress_mpa"),
        "residStress": analysis.get("residual_stress_mpa"),
        "stiffLo": stiff_lo,
        "stiffHi": stiff_hi,
        "globalPeak": analysis.get("global_peak_mpa"),
        "cycles": [_cycle_row(c, points_by_cycle.get(c["Cycle"], [])) for c in cycles],
    }


def build_dashboard_data(
    payloads: list[dict], curves: list[Optional[dict]]
) -> dict:
    """Assemble the `DATA` object the results dashboard template expects.

    `payloads` and `curves` are parallel lists of 1 or `MAX_SPECIMENS` records
    (from `read_json`) and their curve caches (from `read_curve_cache`, or
    None when a cache is missing -- the loop chart just draws nothing for that
    specimen rather than failing). Warnings, strain basis, config and the
    source filename are taken from the FIRST specimen: for a two-specimen
    series ingested together they are identical, which is asserted by the
    caller rather than silently assumed here.
    """
    if not payloads:
        raise ValueError("build_dashboard_data needs at least one specimen")
    if len(payloads) > MAX_SPECIMENS:
        raise ValueError(
            f"the dashboard template supports at most {MAX_SPECIMENS} specimens "
            f"(S1/S2 plus their Avg) -- got {len(payloads)}"
        )

    shorts = [f"S{i + 1}" for i in range(len(payloads))]
    specimens = [
        _specimen_block(p, c, s) for p, c, s in zip(payloads, curves, shorts)
    ]

    first = payloads[0]
    return {
        "specimens": specimens,
        "warnings": first["analysis"].get("warnings", []),
        "strainBasis": first["analysis"].get("strain_basis", {}),
        "sourceFilename": first["specimen"].get("source_filename"),
        "config": first["config"],
    }
