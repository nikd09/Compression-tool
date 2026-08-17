"""
Regression pins against the real exports.

The export files are not committed (they are instrument data, and gitignored).
Drop them into tests/data/ under the names in KNOWN and this module activates;
until then each test skips rather than passing vacuously.

The pins are the values the engine actually produced on the real data, verified
against HANDOFF.md section 2. Their purpose is to make a future change that
shifts a result fail loudly instead of quietly.

Structural facts (cycle counts, metadata, holds, physical bounds) are pinned
tightly. Headline numbers are pinned with tolerance: they should move if the
config is deliberately changed, but not drift on their own.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from compression_tool import Config, ingest, preview
from compression_tool.core import analyse_test, detect_format, load_tests

DATA = Path(__file__).parent / "data"

KNOWN: dict[str, dict] = {
    # Multi-stage compression, two specimens, 50 -> 450 MPa in 50 MPa steps.
    "Mehrstufiger_Druckversuch_Vergleichstest_2_T050E1.xlsx": {
        "format": "series",
        "n_specimens": 2,
        "n_cycles": 9,
        "h0_mm": 0.471,
        "d0_mm": 16.0,
        "temperature_c": 23.0,
        "multi_stage": True,
        # The stress channel is labelled 'Standardkraft' (force) but carries
        # MPa, so this only resolves correctly because units drive the choice.
        "displacement_channel": "Sonder LÄA",
        "stage_peaks_mpa": [50.0, 100.0, 150.0, 200.0, 250.0, 300.0, 350.0, 400.0, 450.0],
        # Cumulative permanent deformation at the last stage, per specimen.
        "final_permdef_pct": {"S1": 13.494, "S2": 14.071},
    },
    # Referenced in HANDOFF.md; not yet supplied.
    "TALCO50.xlsx": {
        "format": "single",
        "n_specimens": 1,
        "n_cycles": 6,
        "h0_mm": None,
        "d0_mm": None,
        "temperature_c": None,
        "multi_stage": True,
        "displacement_channel": None,
        "stage_peaks_mpa": None,
        "final_permdef_pct": None,
    },
}


def _available() -> list[str]:
    return [name for name in KNOWN if (DATA / name).exists()]


def _require(name: str) -> Path:
    path = DATA / name
    if not path.exists():
        pytest.skip(f"{name} not present in tests/data/; drop the export in to enable")
    return path


def _suffix(label: str) -> str:
    return "S" + label.split("_S")[-1]


# ----------------------------------------------------------------------------
# Structure and metadata
# ----------------------------------------------------------------------------


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


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_metadata(name):
    path = _require(name)
    expected = KNOWN[name]

    for test in load_tests(str(path)):
        for key, attr in (("h0_mm", "h0_mm"), ("d0_mm", "d0_mm"),
                          ("temperature_c", "temperature_c")):
            want, got = expected[key], getattr(test, attr)
            if want is None:
                assert got is None, f"{attr} appeared from nowhere"
            else:
                assert got == pytest.approx(want), attr

        if expected["displacement_channel"]:
            assert test.displacement_channel == expected["displacement_channel"]


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_stage_peaks(name):
    """Load-controlled stages must land on their commanded values."""
    path = _require(name)
    expected = KNOWN[name]["stage_peaks_mpa"]
    if expected is None:
        pytest.skip("no stage peaks recorded for this file")

    for test in load_tests(str(path)):
        peaks = analyse_test(test, Config())["PeakStress_MPa"].tolist()
        assert peaks == pytest.approx(expected, rel=1e-3), f"{test.label} stages moved"


# ----------------------------------------------------------------------------
# Physical bounds
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_no_unphysical_values(name):
    """Zero unphysical values was part of the original validation."""
    path = _require(name)

    for test in load_tests(str(path)):
        df = analyse_test(test, Config())

        assert (df["Energy_in_MPa_mm"] > 0).all()
        # Negative dissipation is what splitting the loop at maximum stress
        # rather than maximum displacement produces.
        assert (df["Energy_dissipated_MPa_mm"] >= 0).all(), "loop split is wrong"
        assert df["HysteresisLoss_rel"].between(0, 1).all()
        assert (df["PeakStress_MPa"] > 0).all()
        assert (df["PeakDisp_mm"] > 0).all()

        fitted = df["Stiffness_common_MPa_per_mm"].dropna()
        assert (fitted > 0).all(), "negative stiffness"

        # Read on the loading branch, so it must clear the contact-loss
        # baseline that a zero-referenced reading would return.
        assert (df["ResidualDisp_mm"] > 0).all()
        assert df["PermDef_cumulative_mm"].is_monotonic_increasing


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_holds_are_found(name):
    """Every cycle in both files has a long dwell at peak, 900-3000 samples."""
    path = _require(name)
    for test in load_tests(str(path)):
        df = analyse_test(test, Config())
        assert df["HoldDetected"].all()
        assert df["HoldPoints"].between(800, 3200).all()
        assert df["Creep_during_hold_mm"].notna().all()


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_stiffness_fits_are_well_supported(name):
    """The common band must stay reachable in every stage, with enough samples
    and a good enough fit that the numbers are worth plotting."""
    path = _require(name)
    for test in load_tests(str(path)):
        df = analyse_test(test, Config())
        assert df["Stiffness_common_MPa_per_mm"].notna().all()
        assert (df["Stiffness_common_n"] >= 100).all()
        assert (df["Stiffness_common_r2"] > 0.97).all()


# ----------------------------------------------------------------------------
# Headline values
# ----------------------------------------------------------------------------


def test_permanent_deformation_matches_recorded_values():
    name = "Mehrstufiger_Druckversuch_Vergleichstest_2_T050E1.xlsx"
    path = _require(name)
    expected = KNOWN[name]["final_permdef_pct"]

    for test in load_tests(str(path)):
        df = analyse_test(test, Config())
        final = df["PermDef_cumulative_pct"].iloc[-1]
        assert final == pytest.approx(expected[_suffix(test.label)], rel=1e-3)


def test_series_specimens_agree():
    """The two specimens of the series agreed closely -- the repeatability
    signal that made the original validation credible.

    Peak stress is load-controlled and must match almost exactly. Stiffness is
    the material response and is allowed more room; displacement magnitudes
    scatter considerably more between specimens and are deliberately not
    pinned here.
    """
    path = _require("Mehrstufiger_Druckversuch_Vergleichstest_2_T050E1.xlsx")
    a, b = (analyse_test(t, Config()) for t in load_tests(str(path)))

    assert len(a) == len(b)
    for column, tolerance in (("PeakStress_MPa", 0.01),
                              ("Stiffness_common_MPa_per_mm", 0.15)):
        left, right = a[column], b[column]
        both = left.notna() & right.notna()
        relative = ((left[both] - right[both]).abs() / right[both].abs())
        assert relative.max() < tolerance, f"{column} diverged between specimens"


# ----------------------------------------------------------------------------
# Round trip
# ----------------------------------------------------------------------------


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


@pytest.mark.skipif(
    not (DATA / "Mehrstufiger_Druckversuch_Vergleichstest_2_T050E1.xlsx").exists(),
    reason="series export not present",
)
def test_specimen_columns_of_differing_length_load():
    """The series sheet pads shorter specimen columns with blanks; each
    specimen must be trimmed to its own valid length rather than to the
    shortest or the longest."""
    path = DATA / "Mehrstufiger_Druckversuch_Vergleichstest_2_T050E1.xlsx"
    lengths = {t.label: len(t.stress_mpa) for t in load_tests(str(path))}

    assert len(set(lengths.values())) > 1, "expected the columns to differ in length"
    for label, n in lengths.items():
        assert n > 80_000, f"{label} lost samples"
