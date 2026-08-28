"""
Warnings.

Each of these exists because a number looks identical whether or not the
condition applies. The tests check both that a warning fires when it should and
that it stays quiet when it should not -- a warning that is always on is worth
as little as one that never fires.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from compression_tool import Config, ingest, preview
from compression_tool.core import TestData, analyse_test
from compression_tool.diagnostics import collect, strain_basis
from compression_tool.persistence import read_json

from conftest import H0_MM, STAGES


def _test_data(stress, disp, h0=H0_MM, channel="Sonder LAA") -> TestData:
    return TestData(
        label="synthetic", displacement_mm=disp, stress_mpa=stress,
        source_file="synthetic", source_format="single", h0_mm=h0,
        displacement_channel=channel,
    )


def _codes(warnings) -> set[str]:
    return {w["code"] for w in warnings}


# ----------------------------------------------------------------------------
# Gauge length
# ----------------------------------------------------------------------------


def test_gauge_length_is_unconfirmed_until_asserted(signal):
    stress, disp = signal
    test = _test_data(stress, disp)
    df = analyse_test(test, Config())

    warnings = collect(test, df, Config())
    assert "gauge_length_unconfirmed" in _codes(warnings)
    critical = [w for w in warnings if w["code"] == "gauge_length_unconfirmed"]
    assert critical[0]["severity"] == "critical"
    # The message must name the channel and the height being assumed.
    assert "Sonder LAA" in critical[0]["message"]
    assert "0.471" in critical[0]["message"]

    confirmed = collect(test, df, Config(), gauge_length_confirmed=True)
    assert "gauge_length_unconfirmed" not in _codes(confirmed)


def test_no_h0_reports_absence_rather_than_doubt(signal):
    stress, disp = signal
    test = _test_data(stress, disp, h0=None)
    df = analyse_test(test, Config())

    codes = _codes(collect(test, df, Config()))
    assert "no_gauge_length" in codes
    assert "gauge_length_unconfirmed" not in codes


def test_strain_basis_records_what_was_divided_by(signal):
    stress, disp = signal
    test = _test_data(stress, disp)

    basis = strain_basis(test)
    assert basis["h0_mm"] == pytest.approx(H0_MM)
    assert basis["displacement_channel"] == "Sonder LAA"
    assert basis["gauge_length_confirmed"] is False
    assert basis["strain_valid"] is False

    assert strain_basis(test, gauge_length_confirmed=True)["strain_valid"] is True
    # No h0 means strain can never be valid, whatever is asserted.
    bare = strain_basis(_test_data(stress, disp, h0=None), gauge_length_confirmed=True)
    assert bare["strain_valid"] is False


# ----------------------------------------------------------------------------
# First-cycle discard threshold
# ----------------------------------------------------------------------------


def _two_stage_signal(valley: float) -> tuple[np.ndarray, np.ndarray]:
    """Two stages (peaks 50, 100 MPa), with the valley between them held at
    a chosen level instead of returning to baseline -- lets a test dial in
    exactly how close to the discard margin the first stage sits."""
    n = 200
    stress = np.concatenate([
        np.linspace(0.0, 50.0, n),
        np.linspace(50.0, valley, n),
        np.linspace(valley, 100.0, n),
        np.linspace(100.0, 0.0, n),
    ])
    disp = np.linspace(0.0, 1.0, len(stress))
    return stress, disp


def test_first_cycle_near_discard_threshold_is_flagged():
    """Default unload_frac is 0.5: the valley must give back at least half of
    a candidate's own peak. A valley at 22.5 MPa against a 50 MPa first stage
    gives a ratio of 0.55 -- accepted by the real config, but below the 0.667
    ratio _first_cycle_at_risk probes for (0.5 / FIRST_CYCLE_MARGIN), so the
    warning must fire even though segmentation itself found both stages."""
    stress, disp = _two_stage_signal(valley=22.5)
    test = _test_data(stress, disp)
    df = analyse_test(test, Config())
    assert len(df) == 2

    warnings = collect(test, df, Config())
    assert "first_cycle_near_discard_threshold" in _codes(warnings)
    msg = [w for w in warnings if w["code"] == "first_cycle_near_discard_threshold"][0]
    assert msg["severity"] == "critical"
    assert "50.00 MPa" in msg["message"]


def test_no_warning_when_the_margin_is_comfortable():
    """A valley near zero gives a ratio close to 1.0 -- comfortably clear of
    the probe margin."""
    stress, disp = _two_stage_signal(valley=0.5)
    test = _test_data(stress, disp)
    df = analyse_test(test, Config())
    assert len(df) == 2

    assert "first_cycle_near_discard_threshold" not in _codes(collect(test, df, Config()))


def test_no_false_positive_for_a_genuinely_isolated_single_cycle():
    """A signal with exactly one real local maximum has nothing to be
    fragile RELATIVE TO: by construction its own peak equals the global
    peak (always clears major_cycle_frac) and there is no neighbouring
    valley for the unload_frac ratio test to fail. The old formula was a
    FIXED, uninformative constant at n=1 (identical regardless of the
    signal -- smallest-peak-vs-global-peak are the same value there,
    always) rather than reflecting real segmentation risk; the rewrite must
    not manufacture a false warning to compensate, only report risk where
    a real competing candidate actually exists (see the near-miss-merge
    tests above, which DO fire at whatever cycle count they land on)."""
    n = 200
    stress = np.concatenate([
        np.linspace(0.0, 50.0, n),
        np.linspace(50.0, 0.5, n),  # single clean cycle, no competing peak
    ])
    disp = np.linspace(0.0, 1.0, len(stress))
    test = _test_data(stress, disp)
    df = analyse_test(test, Config())
    assert len(df) == 1

    assert "first_cycle_near_discard_threshold" not in _codes(collect(test, df, Config()))


def test_residual_unreadable_cycles_are_flagged(signal):
    """residual_stress is a fraction of the GLOBAL peak (450 MPa here); at
    0.15 that is 67.5 MPa -- above cycle 1's own 50 MPa peak, so cycle 1
    cannot read the residual reference stress on either branch."""
    stress, disp = signal
    test = _test_data(stress, disp)
    cfg = Config(residual_stress_frac=0.15)
    df = analyse_test(test, cfg)

    assert pd.isna(df["ResidualDisp_mm"].iloc[0])
    assert pd.isna(df["ResidualDisp_unload_mm"].iloc[0])
    assert df["ResidualDisp_mm"].notna().any()

    warnings = collect(test, df, cfg)
    assert "residual_unreadable_cycles" in _codes(warnings)
    msg = [w for w in warnings if w["code"] == "residual_unreadable_cycles"][0]
    assert msg["severity"] == "critical"
    assert "Cycle(s) 1" in msg["message"]


def test_no_residual_warning_when_cycle_1_is_reachable(signal):
    """The default residual_stress_frac (0.02 -> 9 MPa) sits well inside
    cycle 1's own 50 MPa peak, so no warning fires."""
    stress, disp = signal
    test = _test_data(stress, disp)
    df = analyse_test(test, Config())

    assert pd.notna(df["ResidualDisp_mm"].iloc[0])
    assert "residual_unreadable_cycles" not in _codes(collect(test, df, Config()))


def test_discarded_runs_are_reported_with_their_peaks(signal):
    """Raising the threshold past the cliff drops cycle 1; the warning must say
    which run went and how big it was."""
    stress, disp = signal
    test = _test_data(stress, disp)
    cfg = Config(major_cycle_frac=0.15)
    df = analyse_test(test, cfg)

    assert len(df) == len(STAGES) - 1
    warnings = [w for w in collect(test, df, cfg)
                if w["code"] == "cycles_discarded_by_peak_filter"]
    assert warnings
    assert "50.0 MPa" in warnings[0]["message"]


# ----------------------------------------------------------------------------
# Dwell length
# ----------------------------------------------------------------------------


def test_equal_dwells_do_not_trip_the_comparability_warning(signal):
    """The synthetic signal holds every cycle for the same 1000 samples."""
    stress, disp = signal
    test = _test_data(stress, disp)
    df = analyse_test(test, Config())

    assert "variable_dwell_length" not in _codes(collect(test, df, Config()))


def test_unequal_dwells_are_flagged():
    from conftest import BASELINE_MM, cycle_arrays

    parts_s, parts_x = [np.zeros(200)], [np.full(200, BASELINE_MM)]
    x_perm = BASELINE_MM
    for peak, hold_n in ((100.0, 3000), (200.0, 800)):
        s, x = cycle_arrays(peak, x_perm, 0.02, 0.002, 0.001, n_hold=hold_n)
        parts_s.append(s)
        parts_x.append(x)
        x_perm += 0.001

    test = _test_data(np.concatenate(parts_s), np.concatenate(parts_x))
    df = analyse_test(test, Config())

    warnings = [w for w in collect(test, df, Config()) if w["code"] == "variable_dwell_length"]
    assert warnings
    assert "not a creep rate" in warnings[0]["message"].lower()


# ----------------------------------------------------------------------------
# Preload / seating cycle
# ----------------------------------------------------------------------------


def test_a_hold_free_cycle_among_held_ones_is_flagged_as_a_possible_preload():
    """A small, clean, hold-free ramp ahead of several held stages is exactly
    the real signature found on the T050E1 export -- adaptive segmentation
    now correctly finds it as its own cycle (it is real, cleanly-separated
    signal, not noise), and it is kept in the data rather than silently
    dropped. This diagnostic is what keeps that honest instead of letting a
    reader assume every counted cycle is a programmed stage."""
    from conftest import BASELINE_MM, cycle_arrays

    parts_s, parts_x = [np.zeros(200)], [np.full(200, BASELINE_MM)]
    x_perm = BASELINE_MM
    preload_peak = 10.0
    s, x = cycle_arrays(preload_peak, x_perm, 0.01, 0.0, 0.0002, hold=False)
    parts_s.append(s)
    parts_x.append(x)
    x_perm += 0.0002
    for peak in (100.0, 200.0, 300.0):
        s, x = cycle_arrays(peak, x_perm, 0.03, 0.003, 0.001, hold=True)
        parts_s.append(s)
        parts_x.append(x)
        x_perm += 0.001

    test = _test_data(np.concatenate(parts_s), np.concatenate(parts_x))
    df = analyse_test(test, Config())
    assert len(df) == 4
    assert not df["HoldDetected"].iloc[0]
    assert df["HoldDetected"].iloc[1:].all()

    warnings = [w for w in collect(test, df, Config()) if w["code"] == "possible_preload_cycle"]
    assert warnings
    assert warnings[0]["severity"] == "caution"
    assert "Cycle(s) 1" in warnings[0]["message"]


def test_no_preload_warning_when_every_cycle_holds(signal):
    """The ordinary synthetic multi-stage test: every cycle dwells, so there
    is no hold-free outlier to flag."""
    stress, disp = signal
    test = _test_data(stress, disp)
    df = analyse_test(test, Config())

    assert "possible_preload_cycle" not in _codes(collect(test, df, Config()))


def test_no_preload_warning_when_holds_are_the_exception_not_the_norm():
    """A mostly hold-free, fast-cycling test (a legitimate test design of its
    own) is not the preload signature -- only a hold-free MINORITY among an
    otherwise-held test is."""
    from conftest import BASELINE_MM, cycle_arrays

    parts_s, parts_x = [np.zeros(200)], [np.full(200, BASELINE_MM)]
    x_perm = BASELINE_MM
    for peak, hold in ((10.0, False), (20.0, False), (30.0, True)):
        s, x = cycle_arrays(peak, x_perm, 0.01, 0.0, 0.0002, hold=hold)
        parts_s.append(s)
        parts_x.append(x)
        x_perm += 0.0002

    test = _test_data(np.concatenate(parts_s), np.concatenate(parts_x))
    df = analyse_test(test, Config())
    assert len(df) == 3

    assert "possible_preload_cycle" not in _codes(collect(test, df, Config()))


# ----------------------------------------------------------------------------
# Delivery
# ----------------------------------------------------------------------------


def test_warnings_reach_the_record_and_the_preview(workspace, series_file):
    rows = preview([series_file])
    assert all("gauge_length_unconfirmed" in _codes(r["warnings"]) for r in rows)

    result = ingest([series_file], workspace, material="PEEK")
    payload = read_json(result.specimens[0].json_path)
    assert "gauge_length_unconfirmed" in _codes(payload["analysis"]["warnings"])


def test_warnings_are_ordered_worst_first(signal):
    stress, disp = signal
    test = _test_data(stress, disp)
    df = analyse_test(test, Config())

    severities = [w["severity"] for w in collect(test, df, Config())]
    rank = {"critical": 0, "caution": 1, "info": 2}
    assert severities == sorted(severities, key=lambda s: rank[s])


def test_warnings_appear_in_the_html_report(workspace, series_file):
    from compression_tool.html_report import render

    result = ingest([series_file], workspace, material="PEEK")
    page = render(result.payloads)

    assert "Read this before quoting the numbers" in page
    assert "warn-critical" in page
    assert "PROVISIONAL" in page
