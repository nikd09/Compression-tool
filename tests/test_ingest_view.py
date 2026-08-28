"""
ingest_view.py: the Material picker(s) on the Ingest form.

_looks_like_a_filename is the nudge that warns when the typed Material looks
like the export's own file name, not a material code.

_resolve_material_groups and the AppTest scenario below pin the fix for a
real reported bug: Ingest used to have exactly ONE Material field for the
whole upload, so attaching two files meant for two different materials
together silently combined them into one material with every specimen
under a single name -- no warning, no way to split them apart short of
deleting and re-ingesting separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from streamlit.testing.v1 import AppTest

from compression_tool import knowledge_base
from compression_tool.webapp.ingest_view import _looks_like_a_filename, _resolve_material_groups

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class _FakeUpload:
    name: str


def test_a_short_material_code_never_triggers_the_warning():
    assert not _looks_like_a_filename("T050LR1", [_FakeUpload("Mehrstufiger Druckversuch.xlsx")])


def test_the_uploaded_files_own_stem_typed_as_material_triggers_it():
    assert _looks_like_a_filename(
        "Mehrstufiger Druckversuch Vergleichstest 2",
        [_FakeUpload("Mehrstufiger Druckversuch Vergleichstest 2.xlsx")],
    )


def test_a_long_but_unrelated_material_name_does_not_trigger_it():
    assert not _looks_like_a_filename(
        "A Completely Different Long Material Name",
        [_FakeUpload("Mehrstufiger Druckversuch Vergleichstest 2.xlsx")],
    )


def test_empty_material_never_triggers_it():
    assert not _looks_like_a_filename("", [_FakeUpload("Mehrstufiger Druckversuch Vergleichstest 2.xlsx")])


# ----------------------------------------------------------------------------
# _resolve_material_groups
# ----------------------------------------------------------------------------


def test_no_overrides_puts_every_path_in_one_group():
    """The common case -- several files, one material -- must stay exactly
    one ingest() call's worth of paths, not get split for no reason."""
    paths = [Path("a.xlsx"), Path("b.xlsx")]
    groups = _resolve_material_groups(paths, "PEEK-GF30", {})
    assert groups == {"PEEK-GF30": paths}


def test_a_per_file_override_splits_into_two_groups():
    paths = [Path("a.xlsx"), Path("b.xlsx")]
    groups = _resolve_material_groups(paths, "MatA", {1: "MatB"})
    assert groups == {"MatA": [paths[0]], "MatB": [paths[1]]}


def test_every_file_overridden_to_the_same_new_material_stays_one_group():
    paths = [Path("a.xlsx"), Path("b.xlsx")]
    groups = _resolve_material_groups(paths, "MatA", {0: "MatC", 1: "MatC"})
    assert groups == {"MatC": paths}


def test_group_order_follows_first_appearance_in_paths():
    """Commit and Preview both rely on iterating material_groups in a
    stable, predictable order -- first-seen, not alphabetical or by
    override-dict insertion order."""
    paths = [Path("a.xlsx"), Path("b.xlsx"), Path("c.xlsx")]
    groups = _resolve_material_groups(paths, "MatA", {1: "MatB", 2: "MatA"})
    assert list(groups.keys()) == ["MatA", "MatB"]
    assert groups["MatA"] == [paths[0], paths[2]]
    assert groups["MatB"] == [paths[1]]


# ----------------------------------------------------------------------------
# End-to-end: two files, two materials, one Commit click
# ----------------------------------------------------------------------------


def _app() -> None:
    import os

    from compression_tool.persistence import Workspace
    from compression_tool.webapp import ingest_view

    ws = Workspace.at(os.environ["_CT_TEST_WORKSPACE_ROOT"]).ensure()
    ingest_view.render(ws)


def test_two_files_can_be_committed_under_two_different_materials(
    monkeypatch, workspace, single_file, series_file
):
    """The exact scenario reported: two exports uploaded together, meant
    for two different materials. Before this fix there was no way to tell
    Ingest that -- both files landed under whichever single Material was
    typed. This drives the real Streamlit form (upload, per-file material
    picker, Commit) and asserts the workspace ends up with TWO materials,
    each with its own specimen(s) -- not one material with all of them."""
    monkeypatch.setenv("_CT_TEST_WORKSPACE_ROOT", str(workspace))
    at = AppTest.from_function(_app).run()
    assert not at.exception

    single_bytes = single_file.read_bytes()
    series_bytes = series_file.read_bytes()
    at.file_uploader[0].upload(single_file.name, single_bytes, _XLSX_MIME)
    at.file_uploader[0].upload(series_file.name, series_bytes, _XLSX_MIME)
    at.run()
    assert not at.exception

    at.text_input(key="ingest_material_text").set_value("MatA").run()
    assert not at.exception

    # The per-file section only appears once >1 file is attached -- find
    # series_file's own selectbox by its label (the filename) rather than
    # reconstructing its generated key, and point it at a new material.
    pf_select = next(sb for sb in at.selectbox if sb.label == series_file.name)
    pf_select.set_value("+ Add new material…").run()
    pf_new_name = next(
        ti for ti in at.text_input
        if ti.key and ti.key.startswith("ingest_pf_") and ti.key.endswith("_new")
    )
    pf_new_name.set_value("MatB").run()
    assert not at.exception

    at.button[1].click().run()  # [0] = Run preview, [1] = Commit to workspace
    assert not at.exception
    assert at.success  # at least one "Ingested N specimen(s)..." message

    conn = knowledge_base.connect((Path(workspace) / "knowledge_base.db"))
    try:
        materials = knowledge_base.materials(conn)
        assert set(materials) == {"MatA", "MatB"}
        assert len(knowledge_base.list_specimens(conn, "MatA")) == 1
        assert len(knowledge_base.list_specimens(conn, "MatB")) == 2
    finally:
        conn.close()
