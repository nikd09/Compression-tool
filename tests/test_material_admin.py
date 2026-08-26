"""
material_admin.py: renaming and deleting a material.

Renaming has to reach every place a material name is embedded -- specimen
records, run folders, the registry, the derived reports -- without
corrupting the curve caches that live alongside the specimen JSONs it
rewrites (the *.json glob catches both, and only "specimen" in payload
tells them apart). Deleting has to actually clear the index, not just the
files, and never touch a raw export another material's specimen still
points at.
"""

from __future__ import annotations

import pytest

from compression_tool import Workspace, ingest, knowledge_base, load_materials
from compression_tool.material_admin import delete_material, rename_material
from compression_tool.persistence import read_json


def test_rename_updates_every_specimen_and_the_registry(workspace, series_file):
    ws = Workspace.at(workspace).ensure()
    ingest([series_file], ws, material="Old Name")

    result = rename_material(ws, "Old Name", "NewName")
    assert result["material"] == "NewName"
    assert result["renamed_specimens"] == 2
    assert not result["failed"]

    assert load_materials(ws) == ["NewName"]
    conn = knowledge_base.connect(ws.db_path)
    try:
        assert knowledge_base.materials(conn) == ["NewName"]
        specimens = knowledge_base.list_specimens(conn, "NewName")
        assert len(specimens) == 2
    finally:
        conn.close()


def test_rename_does_not_corrupt_curve_caches(workspace, series_file):
    """The bug this pins: *.json matches both <specimen>.json and
    <specimen>.curve.json, and only rewriting records that actually have
    a "specimen" key tells them apart. Getting this wrong corrupts the
    curve cache with a bogus "specimen" key, which then makes
    knowledge_base.rebuild() misindex the cache file itself as if it were
    a specimen record -- and the next read of it crashes with a KeyError
    for the missing "analysis" key."""
    ws = Workspace.at(workspace).ensure()
    ingest([series_file], ws, material="Old Name")
    rename_material(ws, "Old Name", "NewName")

    run_dir = next(ws.processed.glob("NewName_*"))
    curve_files = sorted(p for p in run_dir.glob("*.curve.json"))
    assert len(curve_files) == 2
    for cp in curve_files:
        payload = read_json(cp)
        assert "specimen" not in payload
        assert "cycles" in payload


def test_rename_moves_the_run_folder_and_strips_only_the_old_slug(workspace, single_file):
    """A material slug can itself contain underscores; renaming must strip
    exactly the old slug as a known prefix, not split on the first
    underscore, or part of the old name is left stuck onto the new one
    (regression: "NewName_T050LR1_2026-08-25" instead of
    "NewName_2026-08-25")."""
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="T050LR1_batch1")

    rename_material(ws, "T050LR1_batch1", "NewName")

    run_dirs = list(ws.processed.iterdir())
    assert len(run_dirs) == 1
    assert run_dirs[0].name.startswith("NewName_")
    assert "T050LR1_batch1" not in run_dirs[0].name


def test_rename_regenerates_reports_under_the_new_slug(workspace, series_file):
    ws = Workspace.at(workspace).ensure()
    ingest([series_file], ws, material="Old Name")
    rename_material(ws, "Old Name", "NewName")

    reports = ws.root / "reports"
    assert (reports / "NewName.html").exists()
    assert (reports / "NewName.xlsx").exists()
    assert not (reports / "Old-Name.html").exists()
    assert not (reports / "Old-Name.xlsx").exists()


def test_rename_unknown_material_raises(workspace):
    ws = Workspace.at(workspace).ensure()
    with pytest.raises(ValueError):
        rename_material(ws, "Nothing Here", "NewName")


def test_rename_rejects_an_empty_new_name(workspace, series_file):
    ws = Workspace.at(workspace).ensure()
    ingest([series_file], ws, material="Old Name")
    with pytest.raises(ValueError):
        rename_material(ws, "Old Name", "   ")


def test_delete_removes_records_reports_and_registry_entry(workspace, series_file):
    ws = Workspace.at(workspace).ensure()
    ingest([series_file], ws, material="Gone")

    result = delete_material(ws, "Gone")
    assert result["removed_specimens"] == 2
    assert result["removed_run_dirs"] == 1

    assert load_materials(ws) == []
    assert list(ws.processed.iterdir()) == []
    reports = ws.root / "reports"
    assert not (reports / "Gone.html").exists()
    conn = knowledge_base.connect(ws.db_path)
    try:
        assert knowledge_base.materials(conn) == []
    finally:
        conn.close()


def test_delete_leaves_other_materials_untouched(workspace, series_file, single_file):
    ws = Workspace.at(workspace).ensure()
    ingest([series_file], ws, material="Keep")
    ingest([single_file], ws, material="Gone")

    delete_material(ws, "Gone")

    assert load_materials(ws) == ["Keep"]
    conn = knowledge_base.connect(ws.db_path)
    try:
        assert knowledge_base.materials(conn) == ["Keep"]
    finally:
        conn.close()


def test_delete_without_delete_raw_keeps_the_archived_export(workspace, series_file):
    ws = Workspace.at(workspace).ensure()
    ingest([series_file], ws, material="Gone")
    raw_files_before = list(ws.raw.iterdir())
    assert raw_files_before

    delete_material(ws, "Gone", delete_raw=False)

    assert list(ws.raw.iterdir()) == raw_files_before


def test_delete_raw_preserves_a_file_still_used_by_another_material(
    workspace, series_file
):
    """The same export re-ingested under a second material name shares one
    content-addressed archived copy (persistence.archive_raw). Deleting the
    first material with delete_raw=True must not take that shared file out
    from under the second."""
    ws = Workspace.at(workspace).ensure()
    ingest([series_file], ws, material="First")
    ingest([series_file], ws, material="Second")
    raw_files = list(ws.raw.iterdir())
    assert len(raw_files) == 1  # same content, one archived copy

    delete_material(ws, "First", delete_raw=True)

    assert raw_files[0].exists()
    conn = knowledge_base.connect(ws.db_path)
    try:
        assert knowledge_base.materials(conn) == ["Second"]
    finally:
        conn.close()


def test_delete_unknown_material_raises(workspace):
    ws = Workspace.at(workspace).ensure()
    with pytest.raises(ValueError):
        delete_material(ws, "Nothing Here")
