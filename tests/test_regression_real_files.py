"""
Regression pins against the real exports.

The sample files are not in the repository. Drop them into tests/data/ under
the names below and this module activates automatically -- until then it skips
rather than passing vacuously.

The expected values are the ones the engine was validated against and are
recorded in HANDOFF.md section 2. Their purpose is to make a future change that
shifts a result fail loudly instead of quietly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from compression_tool import Config, ingest, preview
from compression_tool.core import analyse_test, detect_format, load_tests

DATA = Path(__file__).parent / "data"

# name in tests/data -> (export layout, specimens, cycles per specimen, h0)
KNOWN = {
    "Mehrstufiger.xlsx": {
        "format": "series",
        "n_specimens": 2,
        "n_cycles": 9,
        "h0_mm": 0.471,
        "d0_mm": 16.0,
        "multi_stage": True,
    },
    "TALCO50.xlsx": {
        "format": "single",
        "n_specimens": 1,
        "n_cycles": 6,
        "h0_mm": None,
        "d0_mm": None,
        "multi_stage": True,
    },
}


def _available() -> list[str]:
    return [name for name in KNOWN if (DATA / name).exists()]


def _require(name: str) -> Path:
    path = DATA / name
    if not path.exists():
        pytest.skip(f"{name} not present in tests/data/; drop the export in to enable")
    return path


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_format_and_cycle_counts(name):
    path = _require(name)
    expected = KNOWN[name]

    assert detect_format(str(path)) == expected["format"]

    tests = load_tests(str(path))
    assert len(tests) == expected["n_specimens"]

    for test in tests:
        df = analyse_test(test, Config())
        assert len(df) == expected["n_cycles"], f"{test.label} cycle count moved"
        assert df.attrs["multi_stage"] is expected["multi_stage"]

        if expected["h0_mm"] is None:
            assert test.h0_mm is None
        else:
            assert test.h0_mm == pytest.approx(expected["h0_mm"])
            assert test.d0_mm == pytest.approx(expected["d0_mm"])


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_no_unphysical_values(name):
    """Zero unphysical values was part of the original validation."""
    path = _require(name)

    for test in load_tests(str(path)):
        df = analyse_test(test, Config())

        assert (df["Energy_in_MPa_mm"] > 0).all()
        assert (df["Energy_dissipated_MPa_mm"] >= 0).all(), "loop split is wrong"
        assert df["HysteresisLoss_rel"].between(0, 1).all()
        assert (df["PeakStress_MPa"] > 0).all()
        assert (df["PeakDisp_mm"] > 0).all()

        fitted = df["Stiffness_common_MPa_per_mm"].dropna()
        assert (fitted > 0).all(), "negative stiffness"


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_holds_are_found(name):
    """Every cycle in both files had a long dwell at peak."""
    path = _require(name)
    for test in load_tests(str(path)):
        df = analyse_test(test, Config())
        assert df["HoldDetected"].all()
        assert (df["HoldPoints"] >= 20).all()


def test_series_specimens_agree():
    """The two specimens of the series agreed closely -- the repeatability
    signal that made the original validation credible."""
    path = _require("Mehrstufiger.xlsx")
    a, b = (analyse_test(t, Config()) for t in load_tests(str(path)))

    assert len(a) == len(b)
    for column in ("PeakStress_MPa", "Stiffness_common_MPa_per_mm"):
        left, right = a[column], b[column]
        both = left.notna() & right.notna()
        relative = ((left[both] - right[both]).abs() / right[both].abs())
        assert relative.max() < 0.25, f"{column} diverged between specimens"


@pytest.mark.skipif(not _available(), reason="no real exports present")
def test_round_trip_through_persistence(workspace):
    """Whatever is available ingests, persists and indexes without loss."""
    paths = [DATA / name for name in _available()]
    result = ingest(paths, workspace, material="regression")

    assert result.skipped == []
    expected = sum(KNOWN[p.name]["n_specimens"] for p in paths)
    assert len(result.specimens) == expected
    assert result.indexed == expected

    for row, specimen in zip(preview(paths), result.specimens):
        assert row["n_cycles"] == specimen.n_cycles
