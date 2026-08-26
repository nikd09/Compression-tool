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
from compression_tool import knowledge_base as kb
from compression_tool import persistence
from compression_tool.persistence import (
    archive_raw,
    jsonable,
    read_json,
    resolve_run_dir,
    run_dir_name,
    run_fingerprint,
    sha256_file,
    slugify,
    specimen_id,
    workspace_index_root,
    write_manifest,
)


# ----------------------------------------------------------------------------
# workspace_index_root -- see its docstring for the bug this exists to
# prevent: two different workspaces on one machine silently sharing a
# local index. default_index_root() is monkeypatched to a tmp_path-based
# base throughout, so these tests never touch the real machine-wide cache.
# ----------------------------------------------------------------------------


def test_workspace_index_root_differs_for_different_roots(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "default_index_root", lambda: tmp_path / "cache")
    a = workspace_index_root(tmp_path / "workspace-a")
    b = workspace_index_root(tmp_path / "workspace-b")
    assert a != b
    assert a.parent == tmp_path / "cache"
    assert b.parent == tmp_path / "cache"


def test_workspace_index_root_is_stable_for_the_same_root(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "default_index_root", lambda: tmp_path / "cache")
    root = tmp_path / "workspace"
    assert workspace_index_root(root) == workspace_index_root(root)


def test_workspace_index_root_normalizes_equivalent_paths(tmp_path, monkeypatch):
    """A relative path and its resolved absolute form must land on the same
    index -- otherwise the same workspace opened two different ways looks
    like two different workspaces and gets needlessly reindexed."""
    monkeypatch.setattr(persistence, "default_index_root", lambda: tmp_path / "cache")
    (tmp_path / "workspace").mkdir()
    monkeypatch.chdir(tmp_path)
    assert workspace_index_root("workspace") == workspace_index_root(tmp_path / "workspace")


def test_two_workspaces_on_one_machine_do_not_share_an_index(
    tmp_path, series_file, single_file, monkeypatch
):
    """The bug this exists to prevent, reproduced directly: opening a
    second, different workspace from the same machine must never surface
    the first workspace's already-indexed materials under the second
    workspace's name. Confirmed live before this fix -- switching the
    webapp's Workspace field between two real folders on one machine did
    exactly that, because index_root=default_index_root() alone resolves to
    one fixed path no matter which workspace passes it in."""
    monkeypatch.setattr(persistence, "default_index_root", lambda: tmp_path / "cache")

    root_a, root_b = tmp_path / "workspace-a", tmp_path / "workspace-b"
    ws_a = Workspace.at(root_a, index_root=workspace_index_root(root_a))
    ws_b = Workspace.at(root_b, index_root=workspace_index_root(root_b))

    ingest([series_file], ws_a, material="PEEK")
    ingest([single_file], ws_b, material="TALCO50")

    conn_b = kb.connect(ws_b.db_path)
    try:
        assert kb.materials(conn_b) == ["TALCO50"]
    finally:
        conn_b.close()


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


def test_new_workspace_uses_the_explorer_friendly_names(workspace, single_file):
    """A workspace with nothing on disk yet writes under the renamed folders,
    not the old raw_input/processed_output -- those are compatibility names,
    not the current default."""
    result = ingest([single_file], workspace, material="TALCO50")
    ws = result.workspace
    assert ws.raw.name == "Raw exports"
    assert ws.processed.name == "Records"
    assert (ws.root / "Raw exports").exists()
    assert (ws.root / "Records").exists()
    assert not (ws.root / "raw_input").exists()
    assert not (ws.root / "processed_output").exists()


def test_workspace_with_legacy_folders_keeps_using_them(workspace, single_file):
    """A workspace ingested into before this rename must not have to move
    anything: as long as the old folder is the one that already exists (and
    the new one does not), every write continues to land there."""
    (workspace / "raw_input").mkdir(parents=True)
    (workspace / "processed_output").mkdir(parents=True)

    result = ingest([single_file], workspace, material="TALCO50")
    ws = result.workspace

    assert ws.raw.name == "raw_input"
    assert ws.processed.name == "processed_output"
    assert not (ws.root / "Raw exports").exists()
    assert not (ws.root / "Records").exists()
    assert len(list((workspace / "raw_input").iterdir())) == 1
    assert result.run_dir.parent.name == "processed_output"


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
    assert spec["raw_input_path"].startswith("Raw exports/")
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
    assert specimen_id(digest, "a", "M") != specimen_id(digest, "b", "M")
    assert specimen_id("other", "a", "M") != specimen_id(digest, "a", "M")


def test_specimen_id_includes_material(series_file):
    """The same export ingested under two material names must not collide on
    one database row -- specimen_id is the SQLite PRIMARY KEY, so two records
    sharing an ID means INSERT OR REPLACE silently drops one."""
    digest = sha256_file(series_file)
    assert specimen_id(digest, "a", "PEEK") != specimen_id(digest, "a", "PEEK-GF30")


def test_same_export_two_materials_indexes_both_specimens(workspace, single_file):
    a = ingest([single_file], workspace, material="PEEK")
    b = ingest([single_file], workspace, material="PEEK-GF30")

    assert a.specimens[0].specimen_id != b.specimens[0].specimen_id
    conn = kb.connect(a.workspace.db_path)
    try:
        rows = kb.list_specimens(conn)
        assert set(rows["material"]) == {"PEEK", "PEEK-GF30"}
        assert len(rows) == 2
    finally:
        conn.close()


def test_record_points_at_a_recoverable_raw_file(workspace, single_file):
    result = ingest([single_file], workspace, material="TALCO50")
    ws = result.workspace
    payload = read_json(result.specimens[0].json_path)

    archived = ws.root / payload["specimen"]["raw_input_path"]
    assert archived.exists()
    assert sha256_file(archived) == payload["specimen"]["source_sha256"]


def test_archive_originals_false_skips_the_copy_but_keeps_the_hash(workspace, single_file):
    """The hash is what a re-ingest of the same file is detected from, so it
    must survive even when nothing is actually copied into Raw exports/."""
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
    assert result.run_dir.parent.name == "Records"
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


def test_resolve_run_dir_claims_the_folder_it_returns(workspace):
    """The old exists()-then-mkdir version left a window between the check
    and the write; resolve_run_dir now claims the folder itself
    (exclusive-create), so by the time it returns, the folder is already
    this call's and no concurrent caller can also be given it."""
    ws = Workspace.at(workspace).ensure()
    run_dir = resolve_run_dir(ws, "PEEK", "fp-a")
    assert run_dir.exists()
    assert run_dir.name == run_dir_name("PEEK")


def test_resolve_run_dir_never_reuses_an_unfinished_folder(workspace):
    """A folder that exists but has no run.json yet is either mid-write by
    someone else, or was abandoned -- either way it is NOT provably a
    finished run with a matching fingerprint, so it must not be silently
    written into."""
    ws = Workspace.at(workspace).ensure()
    first = resolve_run_dir(ws, "PEEK", "fp-a")
    # Simulates a second, concurrent ingest of DIFFERENT sources for the same
    # material on the same day, racing to claim the same base name before
    # either has written run.json: this is exactly the TOCTOU window the old
    # exists()-then-mkdir implementation left open.
    second = resolve_run_dir(ws, "PEEK", "fp-b")

    assert first != second
    assert first.exists() and second.exists()


def test_resolve_run_dir_still_reuses_a_finished_matching_run(workspace):
    """Once run.json proves a folder is a finished run with the SAME
    fingerprint, resolving it again (the same sources, same config, same
    day) must still land back in that folder -- the exclusive-create change
    must not turn a legitimate re-run into a needless new folder every time."""
    ws = Workspace.at(workspace).ensure()
    run_dir = resolve_run_dir(ws, "PEEK", "fp-a")
    write_manifest(run_dir, material="PEEK", cfg=Config(), fingerprint="fp-a",
                    sources=[], specimens=[])

    again = resolve_run_dir(ws, "PEEK", "fp-a")
    assert again == run_dir


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
