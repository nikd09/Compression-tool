"""
Archive, records and run folders.

The properties worth defending here are the ones that make a result
trustworthy months later: the original export is preserved untouched, the
record is complete enough to reproduce the numbers, and re-running never
silently overwrites a different result.
"""

from __future__ import annotations

import math
import numpy as np
import pytest

from compression_tool import Config, Workspace, ingest, preview
from compression_tool.persistence import (
    archive_raw,
    jsonable,
    read_json,
    run_fingerprint,
    sha256_file,
    slugify,
    specimen_id,
)


# ----------------------------------------------------------------------------
# raw_input
# ----------------------------------------------------------------------------


def test_raw_input_is_content_addressed_and_preserved(workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    archived, digest = archive_raw(single_file, ws)

    assert archived.parent == ws.raw
    assert digest == sha256_file(single_file)
    assert digest.startswith(archived.name.split("_")[0])
    assert sha256_file(archived) == digest


def test_re_archiving_the_same_export_is_a_no_op(workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    first, _ = archive_raw(single_file, ws)
    stamp = first.stat().st_mtime_ns
    second, _ = archive_raw(single_file, ws)

    assert first == second
    assert second.stat().st_mtime_ns == stamp
    assert len(list(ws.raw.iterdir())) == 1


def test_archived_copy_is_read_only(workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    archived, _ = archive_raw(single_file, ws)
    assert not (archived.stat().st_mode & 0o222)


def test_different_exports_do_not_collide(workspace, single_file, series_file):
    ws = Workspace.at(workspace).ensure()
    a, da = archive_raw(single_file, ws)
    b, db = archive_raw(series_file, ws)

    assert a != b and da != db
    assert len(list(ws.raw.iterdir())) == 2


# ----------------------------------------------------------------------------
# JSON safety
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (np.float64(1.5), 1.5),
        (np.int64(3), 3),
        (np.bool_(True), True),
        (float("nan"), None),
        (float("inf"), None),
        (None, None),
        (np.array([1.0, 2.0]), [1.0, 2.0]),
    ],
)
def test_jsonable_converts_numpy_and_missing_values(value, expected):
    assert jsonable(value) == expected


def test_records_contain_no_nan(workspace, series_file):
    """A NaN written into JSON comes back from a rebuild as the string 'NaN'
    and quietly poisons later arithmetic. Missing must be null."""
    result = ingest([series_file], workspace, material="PEEK")

    for specimen in result.specimens:
        text = specimen.json_path.read_text(encoding="utf-8")
        assert "NaN" not in text
        assert "Infinity" not in text

        payload = read_json(specimen.json_path)
        for cycle in payload["cycles"]:
            for key, value in cycle.items():
                assert not (isinstance(value, float) and math.isnan(value)), key


# ----------------------------------------------------------------------------
# Records
# ----------------------------------------------------------------------------


def test_record_is_self_contained(workspace, series_file):
    result = ingest([series_file], workspace, material="PEEK-GF30")
    payload = read_json(result.specimens[0].json_path)

    assert payload["schema_version"] >= 1
    spec = payload["specimen"]
    assert spec["material"] == "PEEK-GF30"
    assert spec["source_format"] == "series"
    assert spec["source_sha256"] == sha256_file(series_file)
    assert spec["raw_input_path"].startswith("raw_input/")
    assert spec["h0_mm"] == pytest.approx(0.471)

    # The exact settings behind the numbers travel with them.
    assert payload["config"]["unload_frac"] == Config().unload_frac
    assert set(payload["config"]) == set(vars(Config()))

    analysis = payload["analysis"]
    assert analysis["n_cycles"] == 9
    assert analysis["multi_stage"] is True
    assert analysis["has_strain"] is True
    assert len(payload["cycles"]) == 9


def test_specimen_id_is_stable_across_runs(workspace, series_file, tmp_path):
    first = ingest([series_file], workspace, material="PEEK")
    second = ingest([series_file], tmp_path / "other", material="PEEK")

    assert [s.specimen_id for s in first.specimens] == [
        s.specimen_id for s in second.specimens
    ]


def test_specimen_id_tracks_content_not_filename(series_file, tmp_path):
    digest = sha256_file(series_file)
    assert specimen_id(digest, "a") != specimen_id(digest, "b")
    assert specimen_id("other", "a") != specimen_id(digest, "a")


def test_record_points_at_a_recoverable_raw_file(workspace, single_file):
    result = ingest([single_file], workspace, material="TALCO50")
    ws = result.workspace
    payload = read_json(result.specimens[0].json_path)

    archived = ws.root / payload["specimen"]["raw_input_path"]
    assert archived.exists()
    assert sha256_file(archived) == payload["specimen"]["source_sha256"]


def test_archive_originals_false_skips_the_copy_but_keeps_the_hash(workspace, single_file):
    """The hash is what a re-ingest of the same file is detected from, so it
    must survive even when nothing is actually copied into raw_input/."""
    result = ingest([single_file], workspace, material="TALCO50", archive_originals=False)
    ws = result.workspace

    assert not ws.raw.exists() or not any(ws.raw.iterdir())
    payload = read_json(result.specimens[0].json_path)
    assert payload["specimen"]["raw_input_path"] is None
    assert payload["specimen"]["source_sha256"] == sha256_file(single_file)


def test_write_reports_false_skips_per_run_excel_csv_html_but_not_the_record(
    workspace, series_file
):
    """json and curve.json are never optional -- everything else the
    combined per-material export and the dashboard depend on is rebuilt from
    them. csv/xlsx/html are the convenience copies this flag controls."""
    result = ingest([series_file], workspace, material="PEEK", write_reports=False)

    for specimen in result.specimens:
        assert specimen.json_path.exists()
        assert specimen.curve_path.exists()
        assert specimen.csv_path is None
        assert specimen.xlsx_path is None
        assert specimen.html_path is None
    assert result.run_xlsx is None
    assert result.run_html is None
    assert not list(result.run_dir.glob("*.xlsx"))
    assert not list(result.run_dir.glob("*.html"))
    assert not list(result.run_dir.glob("*.csv"))

    # The combined per-material export is unaffected -- it is built from the
    # JSON records via the index, not from these per-run report files.
    assert result.material_xlsx is not None and result.material_xlsx.exists()
    assert result.material_html is not None and result.material_html.exists()


# ----------------------------------------------------------------------------
# Run folders
# ----------------------------------------------------------------------------


def test_run_folder_is_named_for_material_and_date(workspace, single_file):
    result = ingest([single_file], workspace, material="TALCO 50/2")
    assert result.run_dir.parent.name == "processed_output"
    assert result.run_dir.name.startswith("TALCO-50-2_")


def test_identical_rerun_reuses_the_folder(workspace, single_file):
    a = ingest([single_file], workspace, material="TALCO50")
    b = ingest([single_file], workspace, material="TALCO50")

    assert a.run_dir == b.run_dir
    assert len(list((a.run_dir.parent).iterdir())) == 1


def test_changed_settings_get_their_own_folder(workspace, single_file):
    """A result produced under different settings must never displace the one
    it should be compared against."""
    a = ingest([single_file], workspace, material="TALCO50")
    b = ingest([single_file], workspace, material="TALCO50",
               cfg=Config(residual_stress_frac=0.05))

    assert a.run_dir != b.run_dir
    assert a.run_dir.exists() and b.run_dir.exists()


def test_run_fingerprint_reacts_to_sources_and_config():
    base = run_fingerprint(["a", "b"], Config())
    assert run_fingerprint(["b", "a"], Config()) == base       # order-insensitive
    assert run_fingerprint(["a"], Config()) != base
    assert run_fingerprint(["a", "b"], Config(unload_frac=0.5)) != base


def test_manifest_lists_sources_and_specimens(workspace, series_file):
    result = ingest([series_file], workspace, material="PEEK")
    manifest = read_json(result.run_dir / "run.json")

    assert manifest["material"] == "PEEK"
    assert len(manifest["sources"]) == 1
    assert manifest["sources"][0]["sha256"] == sha256_file(series_file)
    assert len(manifest["specimens"]) == 2
    assert all(s["n_cycles"] == 9 for s in manifest["specimens"])


def test_material_defaults_to_the_file_stem(workspace, single_file):
    result = ingest([single_file], workspace)
    assert result.material == "TALCO50"


@pytest.mark.parametrize(
    "raw,expected",
    [("PEEK GF30", "PEEK-GF30"), ("a/b\\c", "a-b-c"), ("  ", "unnamed"), ("ok_1.2", "ok_1.2")],
)
def test_slugify(raw, expected):
    assert slugify(raw) == expected


# ----------------------------------------------------------------------------
# Ordering guarantees
# ----------------------------------------------------------------------------


def test_raw_is_archived_even_when_the_analysis_fails(workspace, tmp_path):
    """An export that breaks the engine is still preserved, so it can be
    diagnosed instead of lost."""
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not an excel file at all")

    result = ingest([broken], workspace, material="junk")

    ws = Workspace.at(workspace)
    assert len(list(ws.raw.iterdir())) == 1
    assert result.specimens == []
    assert result.skipped and "broken.xlsx" == result.skipped[0][0]


def test_preview_writes_nothing(workspace, series_file):
    ws = Workspace.at(workspace)
    rows = preview([series_file])

    assert len(rows) == 2
    assert all(r["n_cycles"] == 9 for r in rows)
    assert all(r["n_holds"] == 9 for r in rows)
    assert all(r["multi_stage"] for r in rows)
    assert not ws.root.exists()


def test_preview_reports_failure_instead_of_raising(tmp_path):
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"nope")
    (row,) = preview([broken])
    assert "error" in row
