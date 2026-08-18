"""
Curve cache: the RDP-reduced per-cycle loop points written alongside the
record for charting.

Not part of the frozen JSON contract, so these tests do not touch
test_json_contract.py -- they exist to pin the two things a chart actually
depends on: that the reduced loop still looks like the loop (small enclosed-
area error), and that its endpoints are exact, not approximated, so a chart
built from the cache lines up with the metrics in the record it sits beside.
"""

from __future__ import annotations

import numpy as np
import pytest

from compression_tool import Config, ingest
from compression_tool.core import analyse_test, load_tests
from compression_tool.curve_cache import (
    CACHE_VERSION,
    build_curve_cache,
    read_curve_cache,
    write_curve_cache,
    _rdp_mask,
)
from compression_tool.persistence import read_json


def _shoelace_area(points: list[list[float]]) -> float:
    xy = np.asarray(points, dtype=float)
    x, y = xy[:, 0], xy[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


# ----------------------------------------------------------------------------
# _rdp_mask
# ----------------------------------------------------------------------------


def test_a_straight_line_reduces_to_its_two_endpoints():
    xy = np.column_stack([np.linspace(0, 1, 200), np.linspace(0, 1, 200)])
    mask = _rdp_mask(xy, eps=1e-6)
    assert mask.sum() == 2
    assert mask[0] and mask[-1]


def test_a_sharp_corner_is_kept():
    up = np.column_stack([np.zeros(50), np.linspace(0, 1, 50)])
    across = np.column_stack([np.linspace(0, 1, 50), np.ones(50)])
    xy = np.vstack([up, across])
    mask = _rdp_mask(xy, eps=1e-3)
    # The corner sits at index 49 (end of the vertical run).
    assert mask[49]
    assert 3 <= mask.sum() < len(xy)


def test_endpoints_are_always_kept_regardless_of_eps():
    xy = np.random.default_rng(0).random((100, 2))
    mask = _rdp_mask(xy, eps=10.0)  # eps far larger than any deviation
    assert mask[0] and mask[-1]
    assert mask.sum() == 2


def test_empty_and_short_inputs_do_not_crash():
    assert _rdp_mask(np.empty((0, 2)), eps=0.1).size == 0
    one = _rdp_mask(np.array([[0.0, 0.0]]), eps=0.1)
    assert one.tolist() == [True]
    two = _rdp_mask(np.array([[0.0, 0.0], [1.0, 1.0]]), eps=0.1)
    assert two.tolist() == [True, True]


# ----------------------------------------------------------------------------
# build_curve_cache, against a real analysed signal
# ----------------------------------------------------------------------------


@pytest.fixture
def analysed(single_file):
    cfg = Config()
    test = load_tests(str(single_file), cfg)[0]
    df = analyse_test(test, cfg)
    return test, df


def test_one_cache_cycle_per_analysed_cycle(analysed):
    test, df = analysed
    cache = build_curve_cache(test, df, specimen_id="spec-1")
    assert [c["cycle"] for c in cache["cycles"]] == list(df["Cycle"])


def test_cache_version_and_reduction_stats_are_reported(analysed):
    test, df = analysed
    cache = build_curve_cache(test, df, specimen_id="spec-1")
    assert cache["cache_version"] == CACHE_VERSION
    r = cache["reduction"]
    assert r["algorithm"] == "rdp"
    assert 0 < r["kept_points"] < r["raw_points"]


def test_cycle_endpoints_are_exact_not_approximated(analysed):
    """A chart reads PeakStress_MPa etc. from the record; the curve cache's
    first/last point for each cycle must match the raw signal at that cycle's
    _start/_end exactly, or the loop would not close where the metrics say it
    does."""
    test, df = analysed
    cache = build_curve_cache(test, df, specimen_id="spec-1")
    for a, b, cyc in zip(df["_start"], df["_end"], cache["cycles"]):
        a, b = int(a), int(b)
        first, last = cyc["points"][0], cyc["points"][-1]
        assert first == pytest.approx([test.displacement_mm[a], test.stress_mpa[a]])
        assert last == pytest.approx([test.displacement_mm[b], test.stress_mpa[b]])


def test_reduced_loop_area_is_close_to_the_raw_loop_area(analysed):
    """The point of RDP here is a chart that still reads as the same loop.
    Enclosed area is a cheap, meaningful proxy for that: fitted stiffness and
    hysteresis loss are themselves areas/slopes over this same signal."""
    test, df = analysed
    cache = build_curve_cache(test, df, specimen_id="spec-1")
    for a, b, cyc in zip(df["_start"], df["_end"], cache["cycles"]):
        a, b = int(a), int(b)
        raw = list(zip(test.displacement_mm[a : b + 1], test.stress_mpa[a : b + 1]))
        raw_area = _shoelace_area(raw)
        reduced_area = _shoelace_area(cyc["points"])
        if raw_area > 0:
            assert abs(reduced_area - raw_area) / raw_area < 0.05


def test_a_tighter_eps_keeps_more_points(analysed):
    test, df = analysed
    loose = build_curve_cache(test, df, specimen_id="s", eps=1e-2)
    tight = build_curve_cache(test, df, specimen_id="s", eps=1e-5)
    assert tight["reduction"]["kept_points"] > loose["reduction"]["kept_points"]


# ----------------------------------------------------------------------------
# Disk round-trip and pipeline wiring
# ----------------------------------------------------------------------------


def test_write_then_read_round_trips(tmp_path, analysed):
    test, df = analysed
    cache = build_curve_cache(test, df, specimen_id="spec-1")
    path = write_curve_cache(cache, tmp_path / "spec.curve.json")
    assert path.exists()
    back = read_curve_cache(path)
    assert back == cache


def test_ingest_writes_a_curve_cache_beside_the_record(workspace, single_file):
    result = ingest([single_file], workspace, material="TALCO50")
    specimen = result.specimens[0]
    assert specimen.curve_path.exists()
    assert specimen.curve_path.name.endswith(".curve.json")

    cache = read_curve_cache(specimen.curve_path)
    record = read_json(specimen.json_path)
    assert cache["specimen_id"] == record["specimen"]["specimen_id"]
    assert len(cache["cycles"]) == record["analysis"]["n_cycles"]


def test_curve_cache_is_not_part_of_the_frozen_record(workspace, single_file):
    """The whole point of a sidecar: the JSON record itself gains nothing."""
    result = ingest([single_file], workspace, material="TALCO50")
    record = read_json(result.specimens[0].json_path)
    assert "curve" not in record
    assert "cache_version" not in record
