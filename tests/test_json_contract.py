"""
The frozen JSON contract.

Every consumer -- the workbook, the HTML report, the SQLite index and the UI --
reads records by key name. These tests pin the exact shape of a record so that
renaming, removing or retyping a key becomes a deliberate edit here rather than
something that slips through and silently breaks a screen.

Adding a key is allowed within a version and requires adding it below. Anything
else requires bumping SCHEMA_VERSION.
"""

from __future__ import annotations

import json

import pytest

from compression_tool import Config, ingest
from compression_tool.persistence import read_json
from compression_tool.schema import (
    CONTRACT_ANALYSIS,
    CONTRACT_SPECIMEN,
    CONTRACT_STRAIN_BASIS,
    CONTRACT_TOP_LEVEL,
    CONTRACT_WARNING,
    CONTRACT_WARNING_CODES,
    CYCLE_COLUMNS,
    SCHEMA_VERSION,
)

# The per-cycle keys a record carries. Strain keys appear only when h0 is known.
CONTRACT_CYCLE_ALWAYS = (
    "Cycle", "PeakStress_MPa", "PeakDisp_mm", "MaxDisp_mm",
    "StressAtMaxDisp_MPa", "ResidualDisp_mm", "ResidualDisp_unload_mm",
    "PermDef_cumulative_mm", "PermDef_incremental_mm",
    "Stiffness_common_MPa_per_mm", "Stiffness_common_n", "Stiffness_common_r2",
    "Stiffness_common_lo_MPa", "Stiffness_common_hi_MPa",
    "Stiffness_relative_MPa_per_mm", "Stiffness_relative_n", "Stiffness_relative_r2",
    "Stiffness_relative_lo_MPa", "Stiffness_relative_hi_MPa",
    "Energy_in_MPa_mm", "Energy_dissipated_MPa_mm", "HysteresisLoss_rel",
    "HoldDetected", "HoldPoints", "Creep_during_hold_mm",
    "_start", "_end",
)

CONTRACT_CYCLE_STRAIN = (
    "PeakStrain_pct", "MaxStrain_pct",
    "PermDef_cumulative_pct", "PermDef_incremental_pct", "Creep_pct",
)


@pytest.fixture(scope="module")
def record(tmp_path_factory, series_file):
    result = ingest([series_file], tmp_path_factory.mktemp("contract"), material="PEEK")
    return read_json(result.specimens[0].json_path)


@pytest.fixture(scope="module")
def record_no_strain(tmp_path_factory, single_file):
    result = ingest([single_file], tmp_path_factory.mktemp("contract2"), material="TALCO50")
    return read_json(result.specimens[0].json_path)


# ----------------------------------------------------------------------------
# Shape
# ----------------------------------------------------------------------------


def test_schema_version_is_frozen_at_3():
    """Bumping this is how a breaking change is announced. If this fails,
    confirm every consumer was updated before changing the number.

    Bumped 2 -> 3: ref_stress_mpa (analysis) and DispAtRef_load_mm /
    DispAtRef_unload_mm (cycles) removed -- residual_stress_mpa is now the
    one shared reference stress for both permanent deformation and
    cross-cycle comparison, so a separate mid-range reference (unreachable
    on a small/single-cycle test) and its two now-redundant columns are
    gone rather than kept alongside an identical ResidualDisp_* pair."""
    assert SCHEMA_VERSION == 3


def test_top_level_keys(record):
    assert set(record) == set(CONTRACT_TOP_LEVEL)
    assert record["schema_version"] == SCHEMA_VERSION


def test_specimen_keys(record):
    assert set(record["specimen"]) == set(CONTRACT_SPECIMEN)


def test_analysis_keys(record):
    assert set(record["analysis"]) == set(CONTRACT_ANALYSIS)


def test_strain_basis_keys(record):
    assert set(record["analysis"]["strain_basis"]) == set(CONTRACT_STRAIN_BASIS)


def test_config_keys_match_the_dataclass(record):
    assert set(record["config"]) == set(vars(Config()))


def test_cycle_keys_with_strain(record):
    assert record["analysis"]["has_strain"] is True
    expected = set(CONTRACT_CYCLE_ALWAYS) | set(CONTRACT_CYCLE_STRAIN)
    for cycle in record["cycles"]:
        assert set(cycle) == expected


def test_cycle_keys_without_strain(record_no_strain):
    assert record_no_strain["analysis"]["has_strain"] is False
    for cycle in record_no_strain["cycles"]:
        assert set(cycle) == set(CONTRACT_CYCLE_ALWAYS)


def test_every_contract_cycle_key_is_described_in_the_schema():
    """A key in the record with no entry in CYCLE_COLUMNS would reach the UI
    with no label, unit or definition."""
    described = {c.key for c in CYCLE_COLUMNS}
    assert set(CONTRACT_CYCLE_ALWAYS) <= described
    assert set(CONTRACT_CYCLE_STRAIN) <= described


# ----------------------------------------------------------------------------
# Types
# ----------------------------------------------------------------------------


def test_types_are_stable(record):
    spec, analysis = record["specimen"], record["analysis"]
    assert isinstance(record["schema_version"], int)
    assert isinstance(record["created_utc"], str)
    assert isinstance(spec["specimen_id"], str) and len(spec["specimen_id"]) == 16
    assert isinstance(spec["notes"], list)
    assert isinstance(spec["n_points"], int)
    assert isinstance(analysis["n_cycles"], int)
    assert isinstance(analysis["multi_stage"], bool)
    assert isinstance(analysis["has_strain"], bool)
    assert isinstance(analysis["warnings"], list)
    assert isinstance(record["cycles"], list)


def test_no_nan_or_infinity_anywhere(record):
    """Missing is null. A NaN would come back from json.load as a float that
    poisons arithmetic, and JS JSON.parse rejects the literal outright."""
    text = json.dumps(record)
    assert "NaN" not in text
    assert "Infinity" not in text


def test_numeric_cycle_values_are_numbers_or_null(record):
    for cycle in record["cycles"]:
        for key, value in cycle.items():
            if key in ("HoldDetected",):
                assert isinstance(value, bool)
            else:
                assert value is None or isinstance(value, (int, float)), (key, value)


# ----------------------------------------------------------------------------
# Warnings
# ----------------------------------------------------------------------------


def test_warning_shape_and_codes(record):
    warnings = record["analysis"]["warnings"]
    assert warnings, "the fixture should trip at least the gauge-length warning"
    for w in warnings:
        assert set(w) == set(CONTRACT_WARNING)
        assert w["code"] in CONTRACT_WARNING_CODES
        assert w["severity"] in ("critical", "caution", "info")
        assert isinstance(w["message"], str) and w["message"]


def test_gauge_length_unconfirmed_by_default(record):
    basis = record["analysis"]["strain_basis"]
    assert basis["gauge_length_confirmed"] is False
    assert basis["strain_valid"] is False
    codes = {w["code"] for w in record["analysis"]["warnings"]}
    assert "gauge_length_unconfirmed" in codes


def test_confirming_the_gauge_length_clears_the_warning(tmp_path, series_file):
    result = ingest([series_file], tmp_path / "ws", material="PEEK",
                    gauge_length_confirmed=True)
    payload = read_json(result.specimens[0].json_path)
    basis = payload["analysis"]["strain_basis"]

    assert basis["gauge_length_confirmed"] is True
    assert basis["strain_valid"] is True
    codes = {w["code"] for w in payload["analysis"]["warnings"]}
    assert "gauge_length_unconfirmed" not in codes


def test_missing_h0_reports_no_gauge_length_not_unconfirmed(record_no_strain):
    basis = record_no_strain["analysis"]["strain_basis"]
    assert basis["h0_mm"] is None
    assert basis["strain_valid"] is False
    codes = {w["code"] for w in record_no_strain["analysis"]["warnings"]}
    assert "no_gauge_length" in codes
    assert "gauge_length_unconfirmed" not in codes


# ----------------------------------------------------------------------------
# Round trip
# ----------------------------------------------------------------------------


def test_record_survives_a_json_round_trip(record):
    assert json.loads(json.dumps(record)) == record
