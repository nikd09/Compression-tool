"""ingest_view._looks_like_a_filename: the nudge that warns when the typed
Material looks like the export's own file name, not a material code."""

from __future__ import annotations

from dataclasses import dataclass

from compression_tool.webapp.ingest_view import _looks_like_a_filename


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
