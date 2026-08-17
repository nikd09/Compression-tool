"""
Warnings.

Each of these exists because a number looks identical whether or not the
condition applies. The tests check both that a warning fires when it should and
that it stays quiet when it should not -- a warning that is always on is worth
as little as one that never fires.
"""

from __future__ import annotations

import numpy as np
import pytest

from compression_tool import Config, ingest, preview
from compression_tool.core import TestData, analyse_test
from compression_tool.diagnostics import collect, strain_basis
from compression_tool.persistence import read_json

from conftest import H0_MM, STAGES, multistage_signal


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


def test_first_cycle_near_discard_threshold_is_flagged(signal):
    """The synthetic test rises 50 -> 450, so the first stage sits at 11.1% of
    the global peak against a 10% discard line -- the same thin margin the real
    export has."""
    stress, disp = signal
    test = _test_data(stress, disp)
    df = analyse_test(test, Config())

    warnings = collect(test, df, Config())
    assert "first_cycle_near_discard_threshold" in _codes(warnings)
    msg = [w for w in warnings if w["code"] == "first_cycle_near_discard_threshold"][0]
    # It must say where the cliff is, not merely that one exists.
    assert "0.111" in msg["message"]
    assert "rebases" in msg["message"]


def test_no_warning_when_the_margin_is_comfortable():
    """Constant-amplitude cycles all peak at the global peak, ten times clear
    of the discard line."""
    stress, disp = multistage_signal(stages=(300.0, 300.0, 300.0))
    test = _test_data(stress, disp)
    df = analyse_test(test, Config())

    assert "first_cycle_near_discard_threshold" not in _codes(collect(test, df, Config()))


def test_warning_escalates_to_critical_as_the_margin_closes(signal):
    stress, disp = signal
    test = _test_data(stress, disp)
    # 0.11 is within 1% of the 0.1111 cliff.
    cfg = Config(major_cycle_frac=0.11)
    df = analyse_test(test, cfg)

    warnings = [w for w in collect(test, df, cfg)
                if w["code"] == "first_cycle_near_discard_threshold"]
    assert warnings and warnings[0]["severity"] == "critical"


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
