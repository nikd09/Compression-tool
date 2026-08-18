"""
Engine behaviour on synthetic exports.

These lock down the findings that shaped the engine. Each test names the
failure it prevents, because the value of pinning e.g. non-negative dissipated
energy is entirely in remembering why it could ever have been negative.
"""

from __future__ import annotations

import numpy as np
import pytest

from compression_tool.core import (
    Config,
    TestData,
    analyse_test,
    detect_format,
    load_tests,
    segment_cycles,
)

from conftest import (
    BASELINE_MM,
    D0_MM,
    H0_MM,
    STAGES,
    TEMPERATURE_C,
    expected_permanent_set,
    multistage_signal,
    write_series_workbook,
    write_single_workbook,
)


# ----------------------------------------------------------------------------
# Format detection and loading
# ----------------------------------------------------------------------------


def test_detect_format_distinguishes_the_two_layouts(single_file, series_file):
    assert detect_format(str(single_file)) == "single"
    assert detect_format(str(series_file)) == "series"


def test_single_format_prefers_the_extensometer_channel(single_file):
    """The crosshead signal includes machine compliance, so picking it would
    understate stiffness. The choice must come from the channel name."""
    (test,) = load_tests(str(single_file))

    assert test.displacement_channel == "Sonder LAA"
    assert any("2 displacement channels" in n for n in test.notes)
    # The crosshead channel was scaled by 1.35 in the fixture; the loaded
    # signal must not carry that inflation.
    assert float(np.max(test.displacement_mm)) < 0.07


def test_series_format_reads_every_specimen_and_its_metadata(series_file):
    tests = load_tests(str(series_file))

    assert len(tests) == 2
    assert {t.label.split("_S")[-1] for t in tests} == {"1", "2"}
    for test in tests:
        assert test.h0_mm == pytest.approx(H0_MM)
        assert test.d0_mm == pytest.approx(D0_MM)
        assert test.temperature_c == pytest.approx(TEMPERATURE_C)


def test_micrometre_and_millimetre_exports_agree(tmp_path, signal):
    """Units are parsed from the header row, so the same physical test written
    in µm and in mm must analyse identically."""
    stress, disp = signal
    um = write_series_workbook(tmp_path / "um.xlsx", {"1": (stress, disp)}, disp_unit="µm")
    mm = write_series_workbook(tmp_path / "mm.xlsx", {"1": (stress, disp)}, disp_unit="mm")

    (a,), (b,) = load_tests(str(um)), load_tests(str(mm))
    np.testing.assert_allclose(a.displacement_mm, b.displacement_mm, rtol=1e-9)


def test_stress_unit_synonyms_are_equivalent(tmp_path, signal):
    stress, disp = signal
    mpa = write_series_workbook(tmp_path / "a.xlsx", {"1": (stress, disp)}, disp_unit="mm")
    (a,) = load_tests(str(mpa))

    # The single-format fixture writes N/mm², which must scale identically.
    b_path = write_single_workbook(tmp_path / "b.xlsx", stress, disp, disp)
    (b,) = load_tests(str(b_path))
    np.testing.assert_allclose(a.stress_mpa, b.stress_mpa, rtol=1e-9)


# ----------------------------------------------------------------------------
# Segmentation and holds
# ----------------------------------------------------------------------------


def test_segmentation_finds_every_stage(signal):
    stress, _ = signal
    assert len(segment_cycles(stress, Config())) == len(STAGES)


def test_thresholds_are_relative_not_absolute():
    """The old tool's 5 MPa floor would have discarded every cycle of a low
    stress test. Scaling the whole signal must not change the cycle count."""
    stress, disp = multistage_signal(stages=(0.5, 1.0, 1.5, 2.0))
    assert len(segment_cycles(stress, Config())) == 4

    big, _ = multistage_signal(stages=(500.0, 1000.0, 1500.0, 2000.0))
    assert len(segment_cycles(big, Config())) == 4


def test_cycles_expand_into_the_valleys(signal):
    """A cycle that began above the detection threshold could never be
    interpolated at a reference stress below it, so the residual readout would
    silently go missing."""
    stress, _ = signal
    cfg = Config()
    residual = cfg.residual_stress_frac * float(np.nanmax(stress))

    for start, end in segment_cycles(stress, cfg):
        assert float(stress[start]) < residual


def test_hold_is_detected_in_every_cycle(signal):
    stress, disp = signal
    df = analyse_test(_as_test(stress, disp))
    assert df["HoldDetected"].all()
    assert (df["HoldPoints"] > 900).all()


def test_creep_is_omitted_not_zeroed_when_there_is_no_dwell():
    """A cycle without a dwell must report no creep, not a creep of zero --
    the two mean different things to a reader."""
    stress, disp = multistage_signal(hold=False)
    df = analyse_test(_as_test(stress, disp))

    assert not df["HoldDetected"].any()
    assert df["Creep_during_hold_mm"].isna().all()


# ----------------------------------------------------------------------------
# Metrics
# ----------------------------------------------------------------------------


def test_dissipated_energy_is_never_negative(signal):
    """Splitting the loop at maximum stress rather than maximum displacement
    drives this negative, which is physically impossible."""
    stress, disp = signal
    df = analyse_test(_as_test(stress, disp))

    assert (df["Energy_dissipated_MPa_mm"] > 0).all()
    assert (df["HysteresisLoss_rel"] > 0).all()
    assert (df["HysteresisLoss_rel"] < 1).all()


def test_displacement_peaks_after_stress_does(signal):
    """The specimen keeps creeping while stress is already held constant."""
    stress, disp = signal
    cfg = Config()
    for start, end in segment_cycles(stress, cfg):
        s, x = stress[start : end + 1], disp[start : end + 1]
        assert int(np.argmax(x)) > int(np.argmax(s))


def test_stress_at_max_displacement_equals_peak_on_an_intact_specimen(signal):
    """The synthetic signal stops creeping when the dwell ends, so maximum
    displacement lands at peak stress. A value below the peak means the
    specimen went on compacting while the load was coming off -- the damage
    signature this column exists to catch."""
    stress, disp = signal
    df = analyse_test(_as_test(stress, disp))

    ratio = df["StressAtMaxDisp_MPa"] / df["PeakStress_MPa"]
    assert (ratio > 0.999).all()


def test_stress_at_max_displacement_detects_yielding_through_unload():
    """A specimen that keeps compacting as the load is removed puts its maximum
    displacement partway down the unloading ramp, not at the dwell's end."""
    from conftest import BASELINE_MM, CONTACT_SCALE_MPA

    peak, n_ramp, n_hold, n_unload = 200.0, 400, 600, 400
    s_load = np.linspace(0.0, peak, n_ramp)
    contact = 1.0 - np.exp(-s_load / CONTACT_SCALE_MPA)
    x_load = BASELINE_MM + (BASELINE_MM - BASELINE_MM) * contact + 0.02 * (s_load / peak) ** 0.85
    s_hold = np.full(n_hold, peak)
    x_hold = x_load[-1] + 0.002 * np.linspace(0, 1, n_hold) ** 0.5
    # Unloading: displacement keeps rising over the first third of the ramp
    # before the specimen finally recovers -- continued yielding under falling load.
    s_unload = np.linspace(peak, 0.0, n_unload)
    frac = np.linspace(0, 1, n_unload)
    x_unload = x_hold[-1] + 0.004 * np.sin(np.pi * np.clip(frac / 0.6, 0, 1)) - 0.01 * frac**2
    stress = np.concatenate([np.zeros(200), s_load, s_hold, s_unload, np.full(200, 0.001)])
    disp = np.concatenate([np.full(200, BASELINE_MM), x_load, x_hold, x_unload,
                           np.full(200, BASELINE_MM)])

    df = analyse_test(_as_test(stress, disp))
    assert len(df) == 1
    row = df.iloc[0]
    assert row["StressAtMaxDisp_MPa"] < row["PeakStress_MPa"] * 0.95
    # The maximum is still the maximum, whichever branch it fell on.
    assert row["MaxDisp_mm"] == pytest.approx(float(np.max(disp)))
    # And the energy split still yields non-negative dissipation.
    assert row["Energy_dissipated_MPa_mm"] >= 0


def test_common_band_stiffness_is_comparable_across_stages(signal):
    """The whole point of the common band: an identical stress window in every
    cycle, so a rising stage does not masquerade as a stiffening material."""
    stress, disp = signal
    df = analyse_test(_as_test(stress, disp))

    common = df["Stiffness_common_MPa_per_mm"]
    assert common.notna().all()
    assert common.std() / common.mean() < 0.01

    # The relative band, by contrast, is measured further up the curve on each
    # successive stage, so it rises artificially even though the material has
    # not stiffened. It must NOT be used for cross-stage comparison.
    relative = df["Stiffness_relative_MPa_per_mm"]
    assert relative.is_monotonic_increasing
    assert relative.iloc[-1] > relative.iloc[0] * 1.1


def test_stiffness_reports_its_own_fit_quality(signal):
    """A slope from a handful of points is not trustworthy; n and R2 exist so
    the UI can say so rather than plot it as solid."""
    stress, disp = signal
    df = analyse_test(_as_test(stress, disp))

    assert (df["Stiffness_common_n"] >= 3).all()
    assert (df["Stiffness_common_r2"] > 0.99).all()
    # Later stages cross the fixed window in fewer samples.
    assert df["Stiffness_common_n"].iloc[-1] < df["Stiffness_common_n"].iloc[0]


def test_permanent_deformation_accumulates_to_the_expected_value(signal):
    """Pinned against the closed form of the synthetic signal, so a change in
    how the residual is read shows up as a number rather than a vibe."""
    stress, disp = signal
    df = analyse_test(_as_test(stress, disp))

    cumulative = df["PermDef_cumulative_mm"]
    assert cumulative.iloc[0] == pytest.approx(0.0, abs=1e-12)
    assert cumulative.is_monotonic_increasing

    residual_stress = df.attrs["residual_stress_mpa"]
    assert cumulative.iloc[-1] == pytest.approx(
        expected_permanent_set(STAGES, residual_stress), rel=2e-3
    )
    # Incremental is the difference between neighbours, undefined for cycle 1.
    assert np.isnan(df["PermDef_incremental_mm"].iloc[0])
    assert (df["PermDef_incremental_mm"].iloc[1:] > 0).all()


def test_residual_is_read_above_the_contact_loss_baseline(signal):
    """Reading permanent set at zero stress would return the unloaded baseline
    of a few micrometres and mean nothing."""
    stress, disp = signal
    df = analyse_test(_as_test(stress, disp))
    assert (df["ResidualDisp_mm"] > BASELINE_MM).all()


def test_reference_stress_is_reachable_in_the_smallest_cycle(signal):
    """Tying the reference to the global peak would put it out of reach of the
    early stages of a multi-stage test."""
    stress, disp = signal
    df = analyse_test(_as_test(stress, disp))

    assert df.attrs["ref_stress_mpa"] < df["PeakStress_MPa"].min()
    assert df["DispAtRef_load_mm"].notna().all()
    assert df["DispAtRef_unload_mm"].notna().all()
    # The loop is open at the reference stress: unloading sits to the right.
    assert (df["DispAtRef_unload_mm"] > df["DispAtRef_load_mm"]).all()


def test_multi_stage_is_flagged(signal):
    stress, disp = signal
    assert analyse_test(_as_test(stress, disp)).attrs["multi_stage"] is True

    flat, flat_x = multistage_signal(stages=(200.0, 200.0, 200.0))
    assert analyse_test(_as_test(flat, flat_x)).attrs["multi_stage"] is False


# ----------------------------------------------------------------------------
# Strain normalisation
# ----------------------------------------------------------------------------


def test_strain_columns_appear_only_when_h0_is_known(signal):
    """Suppressed rather than faked when the export carries no h0."""
    stress, disp = signal
    strain_cols = ["PeakStrain_pct", "PermDef_cumulative_pct", "Creep_pct"]

    with_h0 = analyse_test(_as_test(stress, disp, h0=H0_MM))
    assert all(c in with_h0.columns for c in strain_cols)
    assert with_h0["PeakStrain_pct"].iloc[-1] == pytest.approx(
        with_h0["PeakDisp_mm"].iloc[-1] / H0_MM * 100.0
    )

    without = analyse_test(_as_test(stress, disp, h0=None))
    assert not any(c in without.columns for c in strain_cols)


def test_config_h0_is_a_fallback_only(series_file, tmp_path, signal):
    """A metadata sheet wins over the configured fallback; without a sheet the
    fallback applies."""
    stress, disp = signal
    (from_meta,) = [t for t in load_tests(str(series_file), Config(h0_mm=99.0))
                    if t.label.endswith("_S1")]
    assert from_meta.h0_mm == pytest.approx(H0_MM)

    bare = write_series_workbook(
        tmp_path / "bare.xlsx", {"1": (stress, disp)}, with_metadata=False
    )
    (fallback,) = load_tests(str(bare), Config(h0_mm=1.25))
    assert fallback.h0_mm == pytest.approx(1.25)


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------


def _as_test(stress, disp, h0=H0_MM) -> TestData:
    return TestData(
        label="synthetic",
        displacement_mm=disp,
        stress_mpa=stress,
        source_file="synthetic",
        source_format="single",
        h0_mm=h0,
    )
