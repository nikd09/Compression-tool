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

import pandas as pd
import pytest

from compression_tool import Config, ingest, preview
from compression_tool.core import analyse_test, detect_format, load_tests

DATA = Path(__file__).parent / "data"

KNOWN: dict[str, dict] = {
    # Multi-stage compression, two specimens, 50 -> 450 MPa in 50 MPa steps --
    # preceded by a short, hold-free ~10 MPa ramp the adaptive segmentation
    # redesign now correctly reports as its own cycle instead of silently
    # merging it into stage 1 (the same class of bug fixed on the MeshG
    # files this redesign was built against -- see core.segment_cycles).
    # Cleanly separated, real amplitude, genuine full unload back to
    # baseline: almost certainly the machine's preload/seating step, not
    # sensor noise, but also not one of the 9 programmed stages -- it has no
    # detected dwell, unlike every real stage. n_cycles is 10, not 9; the
    # PRELOAD_CYCLE index below marks which one it is so the tests that
    # only make sense for a held, programmed stage can skip it explicitly
    # rather than silently assume every cycle is one.
    "Mehrstufiger_Druckversuch_Vergleichstest_2_T050E1.xlsx": {
        "format": "series",
        "n_specimens": 2,
        "n_cycles": 10,
        "preload_cycle": 1,  # 1-indexed Cycle number; not one of the 9 stages
        "h0_mm": 0.471,
        "d0_mm": 16.0,
        "temperature_c": 23.0,
        "multi_stage": True,
        # The stress channel is labelled 'Standardkraft' (force) but carries
        # MPa, so this only resolves correctly because units drive the choice.
        "displacement_channel": "Sonder LÄA",
        # The 9 PROGRAMMED stages only -- the preload cycle's own peak is not
        # a controlled setpoint near a round number and is deliberately not
        # pinned here; test_stage_peaks compares it separately, loosely.
        "stage_peaks_mpa": [50.0, 100.0, 150.0, 200.0, 250.0, 300.0, 350.0, 400.0, 450.0],
        # Cumulative permanent deformation at the last stage, per specimen.
        # Redefined (core.py): PermDef_incremental_mm is now WITHIN-cycle
        # (residual displacement on the way up vs the way back down, in the
        # SAME cycle) rather than referenced to cycle 1's own reading -- see
        # analyse_test. The two numbers below are not a re-pin of the same
        # quantity; they are the new engine's actual output for the
        # redefined quantity, verified directly against this file.
        "final_permdef_pct": {"S1": 23.275, "S2": 22.344},
    },
    # Referenced in HANDOFF.md; not yet supplied. n_cycles here is the OLD
    # engine's count, unverified against the redesigned segmentation -- if
    # this file is ever dropped into tests/data/, re-check it the same way
    # T050E1 was re-checked above before trusting this pin.
    "TALCO50.xlsx": {
        "format": "single",
        "n_specimens": 1,
        "n_cycles": 6,
        "preload_cycle": None,
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
    """Load-controlled stages must land on their commanded values.

    Excludes the preload cycle, if this file has one (see KNOWN): it is not
    one of the programmed stages, so there is no commanded setpoint for it
    to be checked against.
    """
    path = _require(name)
    expected = KNOWN[name]["stage_peaks_mpa"]
    if expected is None:
        pytest.skip("no stage peaks recorded for this file")
    preload_cycle = KNOWN[name].get("preload_cycle")

    for test in load_tests(str(path)):
        df = analyse_test(test, Config())
        if preload_cycle is not None:
            df = df[df["Cycle"] != preload_cycle]
        peaks = df["PeakStress_MPa"].tolist()
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
    """Every PROGRAMMED stage in both files has a long dwell at peak, 900-3000
    samples. The preload cycle, if this file has one (see KNOWN), is
    excluded deliberately: it having no dwell at all is exactly the signal
    that marks it as a preload/seating step rather than a real stage."""
    path = _require(name)
    preload_cycle = KNOWN[name].get("preload_cycle")
    for test in load_tests(str(path)):
        df = analyse_test(test, Config())
        if preload_cycle is not None:
            df = df[df["Cycle"] != preload_cycle]
        assert df["HoldDetected"].all()
        assert df["HoldPoints"].between(800, 3200).all()
        assert df["Creep_during_hold_mm"].notna().all()


@pytest.mark.parametrize("name", sorted(KNOWN))
def test_stiffness_fits_are_well_supported(name):
    """The common band must stay reachable in every PROGRAMMED stage, with
    enough samples and a good enough fit that the numbers are worth
    plotting. The preload cycle, if this file has one (see KNOWN), sits
    below the common-band window entirely (the window is auto-located on
    the smallest STAGE, not the preload) and correctly reports no fit
    rather than a fabricated one -- excluded deliberately, not a quality
    lapse."""
    path = _require(name)
    preload_cycle = KNOWN[name].get("preload_cycle")
    for test in load_tests(str(path)):
        df = analyse_test(test, Config())
        if preload_cycle is not None:
            df = df[df["Cycle"] != preload_cycle]
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


def test_specimen_keeps_compacting_through_unload_at_the_late_stages():
    """Both specimens stop compacting exactly when the dwell ends for the early
    stages, then start carrying their maximum displacement partway down the
    unloading ramp -- S1 from cycle 6, S2 from cycle 7.

    The post-dwell gain runs 0.02 um through cycle 5 against a 0.024 um signal
    noise floor, then climbs to 2.41 um by cycle 9 -- roughly 100x the noise.
    It is a step, not a drift, which is what makes it a sharper onset marker
    than the stiffness rollover.
    """
    path = _require("Mehrstufiger_Druckversuch_Vergleichstest_2_T050E1.xlsx")
    # Ripple during the dwell means an intact cycle reads a shade under 1.000
    # rather than exactly 1.000; the intact band measures 0.9982-0.9998.
    INTACT = 0.997
    # Cycle numbers below are 1-indexed and include the preload cycle (see
    # KNOWN) as Cycle 1, shifting every real stage's number up by one from
    # the pre-redesign pins (were S1: 6, S2: 7).
    onset = {"S1": 7, "S2": 8}
    preload_cycle = KNOWN["Mehrstufiger_Druckversuch_Vergleichstest_2_T050E1.xlsx"]["preload_cycle"]

    for test in load_tests(str(path)):
        df = analyse_test(test, Config())
        ratio = df["StressAtMaxDisp_MPa"] / df["PeakStress_MPa"]
        label = _suffix(test.label)
        first = onset[label]
        # The preload cycle has no dwell to stabilise this ratio against, so
        # it is not a claim about yielding either way -- excluded from both
        # sides of the intact/yielding split below.
        real_stage = df["Cycle"] != preload_cycle

        # Intact through the early stages: displacement stops when the load does.
        assert (ratio[real_stage & (df["Cycle"] < first)] > INTACT).all(), \
            f"{test.label} yielded through unload earlier than cycle {first}"
        # And it does not recover once it starts.
        assert (ratio[real_stage & (df["Cycle"] >= first)] < INTACT).all(), \
            f"{test.label} stopped yielding through unload after cycle {first}"
        # The final stage is unambiguous in both specimens.
        assert ratio.iloc[-1] < 0.98

    # S1 is the clearer case by a wide margin -- it carries its maximum
    # displacement barely half way down the unloading ramp by cycle 9, where S2
    # only reaches 0.97. S2's onset cycle is the less certain of the two: its
    # first yielding cycle reads 0.9943 against an intact floor of 0.9982, so
    # treat that boundary as indicative rather than sharp.
    a, _ = (analyse_test(t, Config()) for t in load_tests(str(path)))
    assert (a["StressAtMaxDisp_MPa"] / a["PeakStress_MPa"]).min() < 0.55


def test_series_specimens_agree():
    """The two specimens of the series agreed closely -- the repeatability
    signal that made the original validation credible.

    Peak stress is load-controlled and must match almost exactly. Stiffness is
    the material response and is allowed more room; displacement magnitudes
    scatter considerably more between specimens and are deliberately not
    pinned here. The stiffness tolerance widened from 0.15 to 0.20 under the
    auto-located common-band window (core._auto_stiffness_window): each
    specimen's window is found independently from its OWN reference cycle,
    so two otherwise-agreeing specimens can land on a slightly different
    window in a cycle where the loading branch is already a little curved --
    confirmed to be cycle 7 here, right at S1's own onset of yielding through
    unload (see test_specimen_keeps_compacting_through_unload_at_the_late_stages)
    -- rather than a fixed fraction of peak landing on identical bounds by
    construction.
    """
    path = _require("Mehrstufiger_Druckversuch_Vergleichstest_2_T050E1.xlsx")
    a, b = (analyse_test(t, Config()) for t in load_tests(str(path)))

    assert len(a) == len(b)
    for column, tolerance in (("PeakStress_MPa", 0.01),
                              ("Stiffness_common_MPa_per_mm", 0.20)):
        left, right = a[column], b[column]
        both = left.notna() & right.notna()
        relative = ((left[both] - right[both]).abs() / right[both].abs())
        assert relative.max() < tolerance, f"{column} diverged between specimens"


# ----------------------------------------------------------------------------
# Low-peak files: the two real bugs this redesign was built to fix
# ----------------------------------------------------------------------------
#
# Not added to KNOWN / the parametrized structural tests above: these are a
# genuinely different kind of test (a fast, mostly hold-free 3 MPa/stage
# sweep) from T050E1's long-dwell 50-450 MPa sequence, and would fail checks
# calibrated for that ("every cycle has a long dwell", "n >= 100 points in
# the common band") for reasons that have nothing to do with segmentation
# correctness. What belongs here is narrower and specific to what these two
# files exist to prove.

MESHG_3 = "MeshG_3mpa_10cyc_3.xlsx"
MESHG_4 = "MeshG_3mpa_10cyc_4.xlsx"
# Real, load-controlled stage peaks, confirmed by direct inspection of the
# raw signal (see the redesign's own investigation) -- not commanded to a
# round number the way T050E1's 50 MPa steps are, so pinned to what the
# engine actually reads, with a loose tolerance for genuine run-to-run scatter.
MESHG_3_PEAKS = [2.994, 5.955, 8.996, 12.021, 15.027, 18.035, 21.025, 23.962, 26.954, 29.918]
MESHG_4_PEAKS = [3.010, 6.005, 9.047, 12.727, 15.037, 18.028, 21.019, 23.996, 26.981, 29.910]


@pytest.mark.parametrize("name,expected_peaks", [(MESHG_3, MESHG_3_PEAKS), (MESHG_4, MESHG_4_PEAKS)])
def test_all_ten_stages_are_found(name, expected_peaks):
    """The two real, reproduced failures this redesign fixes, both on these
    files: MESHG_3's stage 1 (2.994 MPa) used to sit 0.002 MPa below the old
    fixed 10%-of-global-peak discard line -- a coin flip against sensor
    noise for any ~10-stage test, since stage 1 sits at ~1/N of the global
    peak by construction. MESHG_4's stage 1 used to be silently merged into
    stage 2, because the valley between them only relaxes to ~0.85 MPa
    (confirmed by direct inspection), not near zero, and the old absolute
    unload_frac floor (0.02 x global peak = 0.6 MPa) never saw a gap there.
    Both are fixed by locally-adaptive segmentation (core.segment_cycles),
    not by retuning either old threshold -- see its docstring."""
    path = DATA / name
    if not path.exists():
        pytest.skip(f"{name} not present in tests/data/; drop the export in to enable")

    tests = load_tests(str(path))
    assert len(tests) == 1
    df = analyse_test(tests[0], Config())
    assert len(df) == 10, f"{name}: expected 10 stages, found {len(df)}"
    assert df["PeakStress_MPa"].tolist() == pytest.approx(expected_peaks, rel=5e-3)


@pytest.mark.parametrize("name", [MESHG_3, MESHG_4])
def test_residual_reading_is_never_falsely_negative(name):
    """The unloading-branch residual reading (ResidualDisp_unload_mm) is a
    genuinely new risk this redesign introduces, not a reused-and-proven
    path (see core._interp_on_branch and analyse_test) -- specifically
    closest to the contact-loss region on exactly this kind of low-peak
    file. It must never fabricate a negative or otherwise unphysical
    displacement; where the residual reference stress genuinely is not
    reachable on a cycle this small (confirmed to happen on MESHG_4 --
    see test_residual_unreadable_cycle_is_flagged below), it must report
    NaN, visibly, not a wrong number silently."""
    path = DATA / name
    if not path.exists():
        pytest.skip(f"{name} not present in tests/data/; drop the export in to enable")

    df = analyse_test(load_tests(str(path))[0], Config())
    for col in ("ResidualDisp_mm", "ResidualDisp_unload_mm"):
        vals = df[col].dropna()
        assert (vals > 0).all(), f"{name}.{col} produced a non-positive reading"
    # Wherever both branches WERE readable, permanent deformation is a real,
    # non-negative running total -- never negative, even across a cycle
    # that itself reported NaN (pandas cumsum resumes cleanly; see
    # analyse_test).
    cumulative = df["PermDef_cumulative_mm"].dropna()
    assert cumulative.is_monotonic_increasing, f"{name}: cumulative permanent set went backwards"


def test_residual_unreadable_cycle_is_flagged():
    """MESHG_4 cycle 2's own loading-branch slice begins ABOVE the residual
    reference stress (its start boundary is the ~0.85 MPa valley from
    test_all_ten_stages_are_found's fix, not near zero) -- ResidualDisp_mm
    is genuinely unreadable for that one cycle, by construction, not a bug.
    The new diagnostic must say so rather than leave a silent NaN."""
    from compression_tool.diagnostics import collect

    path = DATA / MESHG_4
    if not path.exists():
        pytest.skip(f"{MESHG_4} not present in tests/data/; drop the export in to enable")

    test = load_tests(str(path))[0]
    df = analyse_test(test, Config())
    assert pd.isna(df.loc[df["Cycle"] == 2, "ResidualDisp_mm"]).all()

    codes = {w["code"] for w in collect(test, df, Config())}
    assert "residual_unreadable_cycles" in codes


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
