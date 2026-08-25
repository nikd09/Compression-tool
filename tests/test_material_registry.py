"""
material_registry.py: the controlled list of material names.

Its one job is to stop "SteelMesh", "Steel Mesh" and "steel-mesh" from
becoming three materials that never compare against each other -- these
tests exist to pin exactly that, and to pin that a missing or unreadable
list never blocks ingest.
"""

from __future__ import annotations

import pytest

from compression_tool import Workspace, add_material, ingest, load_materials
from compression_tool.material_registry import _normalize


def test_add_material_registers_a_new_name(workspace):
    ws = Workspace.at(workspace).ensure()
    assert load_materials(ws) == []

    canonical = add_material(ws, "PEEK-GF30")
    assert canonical == "PEEK-GF30"
    assert load_materials(ws) == ["PEEK-GF30"]


def test_near_duplicate_resolves_to_the_existing_canonical_name(workspace):
    ws = Workspace.at(workspace).ensure()
    add_material(ws, "SteelMesh")

    for near_duplicate in ("Steel Mesh", "steel-mesh", "STEEL_MESH", "  SteelMesh  "):
        assert add_material(ws, near_duplicate) == "SteelMesh"

    # Only one material ever got saved, not four.
    assert load_materials(ws) == ["SteelMesh"]


def test_empty_name_is_rejected(workspace):
    ws = Workspace.at(workspace).ensure()
    with pytest.raises(ValueError):
        add_material(ws, "   ")


def test_load_materials_falls_back_to_the_index_when_the_file_is_missing(
    workspace, series_file
):
    """materials.json never existing (a workspace ingested before this
    feature existed) must not mean the list looks empty forever -- it
    should show what is actually there, derived from the index."""
    ws = Workspace.at(workspace).ensure()
    (ws.root / "materials.json").unlink(missing_ok=True)
    ingest([series_file], ws, material="PEEK")
    (ws.root / "materials.json").unlink()  # ingest() just wrote one; remove it again

    assert load_materials(ws) == ["PEEK"]


def test_load_materials_survives_a_corrupt_file(workspace, series_file):
    ws = Workspace.at(workspace).ensure()
    ingest([series_file], ws, material="PEEK")
    (ws.root / "materials.json").write_text("{not valid json", encoding="utf-8")

    assert load_materials(ws) == ["PEEK"]  # recovered from the index instead


def test_ingest_registers_the_material_from_every_entry_point(workspace, series_file):
    """No caller has to remember to call add_material separately -- ingest()
    itself does it, so the list can never drift from what was really ingested."""
    ingest([series_file], workspace, material="TALCO50")
    ws = Workspace.at(workspace)
    assert load_materials(ws) == ["TALCO50"]


def test_ingest_normalizes_a_near_duplicate_material_name(workspace, series_file, single_file):
    """A second ingest under a near-duplicate spelling must land in the SAME
    material as the first, not silently fork into a second one -- this is
    the actual bug the whole feature exists to prevent."""
    ingest([series_file], workspace, material="SteelMesh")
    result = ingest([single_file], workspace, material="steel-mesh")

    assert result.material == "SteelMesh"
    ws = Workspace.at(workspace)
    assert load_materials(ws) == ["SteelMesh"]


@pytest.mark.parametrize(
    "a,b",
    [
        ("SteelMesh", "Steel Mesh"),
        ("SteelMesh", "steel-mesh"),
        ("SteelMesh", "STEEL_MESH"),
        ("PEEK-GF30", "peekgf30"),
    ],
)
def test_normalize_treats_case_and_separators_as_equivalent(a, b):
    assert _normalize(a) == _normalize(b)


def test_normalize_still_distinguishes_different_materials():
    assert _normalize("PEEK") != _normalize("PEEK-GF30")
