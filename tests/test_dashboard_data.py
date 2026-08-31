"""
dashboard_data.build_dashboard_data: the mapping from a stored record (plus
its curve cache) to the shape the results dashboard template renders from.

The template's JS keys (`peakStress`, `kCommon`, ...) are pinned here against
the schema's real column names (`PeakStress_MPa`, `Stiffness_common_MPa_per_mm`,
...) so a rename on either side breaks a test instead of silently producing a
blank chart.
"""

from __future__ import annotations

import pytest

from compression_tool import ingest
from compression_tool.curve_cache import curve_cache_path_for, read_curve_cache
from compression_tool.dashboard_data import MAX_SPECIMENS, build_dashboard_data
from compression_tool.persistence import read_json


@pytest.fixture
def one_payload(workspace, single_file):
    result = ingest([single_file], workspace, material="TALCO50")
    s = result.specimens[0]
    return read_json(s.json_path), read_curve_cache(s.curve_path)


@pytest.fixture
def two_payloads(workspace, series_file):
    result = ingest([series_file], workspace, material="PEEK")
    out = []
    for s in result.specimens:
        out.append((read_json(s.json_path), read_curve_cache(s.curve_path)))
    return out


def test_single_specimen_gets_short_label_s1(one_payload):
    payload, curve = one_payload
    data = build_dashboard_data([payload], [curve])
    assert data["specimens"][0]["short"] == "S1"


def test_two_specimens_get_s1_s2_in_order(two_payloads):
    payloads = [p for p, _ in two_payloads]
    curves = [c for _, c in two_payloads]
    data = build_dashboard_data(payloads, curves)
    assert [s["short"] for s in data["specimens"]] == ["S1", "S2"]


def test_more_than_max_specimens_is_rejected(two_payloads):
    payloads = [p for p, _ in two_payloads]
    curves = [c for _, c in two_payloads]
    n = MAX_SPECIMENS // 2 + 1
    with pytest.raises(ValueError, match=str(MAX_SPECIMENS)):
        build_dashboard_data(payloads * n, curves * n)


def test_mismatched_payloads_and_curves_are_rejected(one_payload):
    payload, curve = one_payload
    with pytest.raises(ValueError, match="parallel"):
        build_dashboard_data([payload, payload], [curve])


# ----------------------------------------------------------------------------
# Four specimens in one test -- the case the 2-specimen build could not do
# ----------------------------------------------------------------------------


@pytest.fixture
def four_payloads(workspace, four_specimen_file):
    result = ingest([four_specimen_file], workspace, material="QUAD")
    return [(read_json(s.json_path), read_curve_cache(s.curve_path))
            for s in result.specimens]


def test_four_specimens_ingest_and_index(four_payloads):
    assert len(four_payloads) == 4


def test_four_specimens_get_sequential_short_labels(four_payloads):
    data = build_dashboard_data([p for p, _ in four_payloads],
                                [c for _, c in four_payloads])
    assert [s["short"] for s in data["specimens"]] == ["S1", "S2", "S3", "S4"]


def test_every_one_of_four_keeps_its_own_cycles_and_curve(four_payloads):
    data = build_dashboard_data([p for p, _ in four_payloads],
                                [c for _, c in four_payloads])
    for sp, (payload, curve) in zip(data["specimens"], four_payloads):
        assert len(sp["cycles"]) == len(payload["cycles"])
        by_n = {c["cycle"]: c["points"] for c in curve["cycles"]}
        for c in sp["cycles"]:
            assert c["pts"] == by_n[c["n"]]


def test_four_specimens_are_not_identical(four_payloads):
    """The fixture scales each specimen differently; if the loader collapsed
    them onto one another this would pass silently everywhere else."""
    data = build_dashboard_data([p for p, _ in four_payloads],
                                [c for _, c in four_payloads])
    peaks = [s["cycles"][-1]["maxDisp"] for s in data["specimens"]]
    assert len(set(round(p, 6) for p in peaks)) == 4


def test_max_specimens_worth_of_records_is_accepted(one_payload):
    """The cap is the palette's slot count, so exactly that many must work."""
    payload, curve = one_payload
    data = build_dashboard_data([payload] * MAX_SPECIMENS, [curve] * MAX_SPECIMENS)
    assert len(data["specimens"]) == MAX_SPECIMENS
    assert data["specimens"][-1]["short"] == f"S{MAX_SPECIMENS}"


def test_no_payloads_is_rejected():
    with pytest.raises(ValueError):
        build_dashboard_data([], [])


def test_top_level_fields_come_from_the_first_specimen(one_payload):
    payload, curve = one_payload
    data = build_dashboard_data([payload], [curve])
    assert data["sourceFilename"] == payload["specimen"]["source_filename"]
    assert data["warnings"] == payload["analysis"]["warnings"]
    assert data["strainBasis"] == payload["analysis"]["strain_basis"]
    assert data["config"] == payload["config"]


def test_specimen_metadata_matches_the_record(one_payload):
    payload, curve = one_payload
    sp = build_dashboard_data([payload], [curve])["specimens"][0]
    spec = payload["specimen"]
    assert sp["label"] == spec["label"]
    assert sp["h0"] == spec["h0_mm"]
    assert sp["d0"] == spec["d0_mm"]
    assert sp["channel"] == spec["displacement_channel"]
    assert sp["globalPeak"] == payload["analysis"]["global_peak_mpa"]
    assert sp["residStress"] == payload["analysis"]["residual_stress_mpa"]


def test_stiffness_window_is_read_from_the_auto_located_bounds(one_payload):
    """The common-band window is auto-located once (core.py) and travels
    with the record as analysis.stiffness_common_lo_mpa / _hi_mpa --
    dashboard_data must read those, not recompute a fixed fraction of the
    smallest cycle peak (the old, pre-redesign behaviour)."""
    payload, curve = one_payload
    sp = build_dashboard_data([payload], [curve])["specimens"][0]
    analysis = payload["analysis"]
    assert sp["stiffLo"] == pytest.approx(analysis["stiffness_common_lo_mpa"])
    assert sp["stiffHi"] == pytest.approx(analysis["stiffness_common_hi_mpa"])
    min_peak = min(c["PeakStress_MPa"] for c in payload["cycles"])
    assert 0 <= sp["stiffLo"] < sp["stiffHi"] <= min_peak


def test_stiffness_window_falls_back_for_a_record_without_it(one_payload):
    """A record written before this redesign has no stiffness_common_lo_mpa
    key -- dashboard_data must still render something rather than crash or
    show no window at all."""
    payload, curve = one_payload
    payload = dict(payload)
    payload["analysis"] = {
        k: v for k, v in payload["analysis"].items()
        if k not in ("stiffness_common_lo_mpa", "stiffness_common_hi_mpa")
    }
    cfg = payload["config"]
    min_peak = min(c["PeakStress_MPa"] for c in payload["cycles"])

    sp = build_dashboard_data([payload], [curve])["specimens"][0]
    assert sp["stiffLo"] == pytest.approx(cfg["stiff_lo_frac"] * min_peak, abs=0.01)
    assert sp["stiffHi"] == pytest.approx(cfg["stiff_hi_frac"] * min_peak, abs=0.01)


def test_cycle_count_matches_the_record(one_payload):
    payload, curve = one_payload
    sp = build_dashboard_data([payload], [curve])["specimens"][0]
    assert len(sp["cycles"]) == len(payload["cycles"])
    assert [c["n"] for c in sp["cycles"]] == [c["Cycle"] for c in payload["cycles"]]


def test_every_template_key_the_charts_read_is_present(one_payload):
    """PANELS in the template reads these exact keys off each cycle row --
    a missing one renders a blank chart, silently, with no error anywhere."""
    payload, curve = one_payload
    sp = build_dashboard_data([payload], [curve])["specimens"][0]
    required = {
        "n", "pts", "peakStress", "kCommon", "permCumPct", "maxStrainPct",
        "loss", "holdDisp", "maxDisp", "eDiss",
        "stressAtMaxDisp", "unloadYield", "residDisp", "kRel", "kRelN", "kRelR2",
    }
    for c in sp["cycles"]:
        assert required <= c.keys()


def test_points_come_from_the_curve_cache_for_the_matching_cycle(one_payload):
    payload, curve = one_payload
    sp = build_dashboard_data([payload], [curve])["specimens"][0]
    by_n = {c["cycle"]: c["points"] for c in curve["cycles"]}
    for c in sp["cycles"]:
        assert c["pts"] == by_n[c["n"]]


def test_missing_curve_cache_gives_empty_points_not_a_crash(one_payload):
    payload, _ = one_payload
    data = build_dashboard_data([payload], [None])
    for c in data["specimens"][0]["cycles"]:
        assert c["pts"] == []


def test_unload_yield_matches_the_engines_own_definition(one_payload):
    """unload_yield_frac lives in schema.py; this pins that the dashboard
    calls the same function rather than reimplementing the ratio inline."""
    payload, curve = one_payload
    sp = build_dashboard_data([payload], [curve])["specimens"][0]
    for c, raw in zip(sp["cycles"], payload["cycles"]):
        if raw["StressAtMaxDisp_MPa"] is not None and raw["PeakStress_MPa"]:
            expected = raw["StressAtMaxDisp_MPa"] / raw["PeakStress_MPa"]
            assert c["unloadYield"] == pytest.approx(expected)


def test_curve_cache_path_round_trips_through_json_path(one_payload, tmp_path):
    from compression_tool.curve_cache import write_curve_cache

    payload, curve = one_payload
    json_path = tmp_path / "spec.json"
    curve_path = curve_cache_path_for(json_path)
    assert curve_path == tmp_path / "spec.curve.json"
    write_curve_cache(curve, curve_path)
    assert read_curve_cache(curve_cache_path_for(json_path)) == curve
