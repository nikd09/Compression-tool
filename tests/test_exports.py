"""
Workbook, CSV and HTML output.

The export is where a reader who was not in the room meets the data, so the
tests care as much about what the file explains as about what it contains: the
two stiffness columns must be distinguishable, the permanent-deformation column
must not claim to be compression set, and a thin fit must be visibly flagged.
"""

from __future__ import annotations

import pandas as pd
import pytest
from openpyxl import load_workbook

from compression_tool import ingest
from compression_tool.excel_export import cycles_dataframe, write_csv, write_workbook
from compression_tool.html_report import render
from compression_tool.persistence import read_json
from compression_tool.schema import (
    QUALITY_MIN_POINTS,
    QUALITY_MIN_R2,
    stiffness_quality,
    user_facing_cycle_columns,
)


@pytest.fixture
def payloads(workspace, series_file):
    result = ingest([series_file], workspace, material="PEEK")
    return [read_json(s.json_path) for s in result.specimens]


@pytest.fixture
def single_payload(workspace, single_file):
    result = ingest([single_file], workspace, material="TALCO50")
    return read_json(result.specimens[0].json_path)


# ----------------------------------------------------------------------------
# Workbook structure
# ----------------------------------------------------------------------------


def test_workbook_has_the_expected_sheets(tmp_path, single_payload):
    path = write_workbook([single_payload], tmp_path / "out.xlsx")
    book = load_workbook(path)
    assert book.sheetnames == ["Summary", "Cycles", "Data dictionary", "Config"]


def test_cycle_headers_carry_units(tmp_path, single_payload):
    path = write_workbook([single_payload], tmp_path / "out.xlsx")
    headers = [c.value for c in load_workbook(path)["Cycles"][1]]

    assert "Peak stress (MPa)" in headers
    assert "Stiffness (common band) (MPa/mm)" in headers
    assert "Stiffness (relative band) (MPa/mm)" in headers
    assert "Creep during hold (mm)" in headers
    # Internal bookkeeping stays out of the user-facing table.
    assert not any(str(h).startswith("_") for h in headers if h)


def test_cycle_rows_match_the_record(tmp_path, single_payload):
    path = write_workbook([single_payload], tmp_path / "out.xlsx")
    sheet = load_workbook(path)["Cycles"]
    headers = [c.value for c in sheet[1]]
    rows = list(sheet.iter_rows(min_row=2, values_only=True))

    assert len(rows) == len(single_payload["cycles"])
    peak_col = headers.index("Peak stress (MPa)")
    for row, cycle in zip(rows, single_payload["cycles"]):
        assert row[peak_col] == pytest.approx(cycle["PeakStress_MPa"])


def test_multi_specimen_workbook_labels_each_row(tmp_path, payloads):
    path = write_workbook(payloads, tmp_path / "run.xlsx")
    sheet = load_workbook(path)["Cycles"]

    assert sheet.cell(row=1, column=1).value == "Specimen"
    labels = {sheet.cell(row=r, column=1).value for r in range(2, sheet.max_row + 1)}
    assert labels == {p["specimen"]["label"] for p in payloads}
    assert sheet.max_row == 1 + sum(len(p["cycles"]) for p in payloads)


def test_summary_compares_specimens_side_by_side(tmp_path, payloads):
    path = write_workbook(payloads, tmp_path / "run.xlsx")
    sheet = load_workbook(path)["Summary"]

    header = [c.value for c in sheet[3]]
    assert header[0] == "Field"
    assert set(header[1:]) == {p["specimen"]["label"] for p in payloads}

    fields = {sheet.cell(row=r, column=1).value for r in range(4, sheet.max_row + 1)}
    assert "Specimen height h0 (mm)" in fields
    assert "Cycles" in fields
    assert "Total permanent deformation (mm)" in fields


def test_data_dictionary_explains_the_traps(tmp_path, single_payload):
    """A reader with only the workbook must be able to tell the two stiffness
    columns apart and must not mistake permanent deformation for compression
    set."""
    path = write_workbook([single_payload], tmp_path / "out.xlsx")
    sheet = load_workbook(path)["Data dictionary"]
    text = "\n".join(
        str(v) for row in sheet.iter_rows(values_only=True) for v in row if v
    )

    assert "IDENTICAL stress window" in text
    assert "NOT comparable across cycles" in text
    assert "ASTM D395" in text
    assert "not seconds" in text


def test_config_sheet_records_the_settings(tmp_path, single_payload):
    path = write_workbook([single_payload], tmp_path / "out.xlsx")
    sheet = load_workbook(path)["Config"]
    pairs = {
        sheet.cell(row=r, column=1).value: sheet.cell(row=r, column=2).value
        for r in range(4, sheet.max_row + 1)
    }

    assert pairs["unload_frac"] == pytest.approx(0.02)
    assert pairs["ref_stress_mpa"] == "auto"
    assert any("reference stress (MPa)" in str(k) for k in pairs)


def test_strain_columns_are_absent_without_h0(tmp_path, single_payload):
    assert single_payload["analysis"]["has_strain"] is False
    path = write_workbook([single_payload], tmp_path / "out.xlsx")
    headers = [c.value for c in load_workbook(path)["Cycles"][1]]

    assert not any("strain" in str(h).lower() for h in headers if h)
    assert not any(str(h).endswith("(%)") for h in headers if h)


def test_strain_columns_are_present_with_h0(tmp_path, payloads):
    assert payloads[0]["analysis"]["has_strain"] is True
    path = write_workbook(payloads, tmp_path / "run.xlsx")
    headers = [c.value for c in load_workbook(path)["Cycles"][1]]

    assert "Peak strain (%)" in headers


# ----------------------------------------------------------------------------
# Quality flag
# ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n,r2,expected",
    [
        (200, 0.999, "ok"),
        (QUALITY_MIN_POINTS - 1, 0.999, "few points"),
        (200, QUALITY_MIN_R2 - 0.01, "nonlinear"),
        (None, None, "none"),
        (3, 0.10, "few points"),
    ],
)
def test_stiffness_quality_flag(n, r2, expected):
    assert stiffness_quality(n, r2) == expected


def test_quality_column_sits_next_to_the_fit_it_describes():
    cols = [c.key for c in user_facing_cycle_columns(has_strain=False)]
    assert cols.index("Stiffness_common_quality") == cols.index("Stiffness_common_r2") + 1


def test_workbook_carries_the_quality_column(tmp_path, single_payload):
    path = write_workbook([single_payload], tmp_path / "out.xlsx")
    sheet = load_workbook(path)["Cycles"]
    headers = [c.value for c in sheet[1]]
    col = headers.index("Stiffness (common band), quality") + 1

    values = {sheet.cell(row=r, column=col).value for r in range(2, sheet.max_row + 1)}
    assert values <= {"ok", "few points", "nonlinear", "none"}


# ----------------------------------------------------------------------------
# CSV
# ----------------------------------------------------------------------------


def test_csv_matches_the_workbook_columns(tmp_path, single_payload):
    csv_path = write_csv([single_payload], tmp_path / "out.csv")
    frame = pd.read_csv(csv_path)
    headers = [c.value for c in load_workbook(
        write_workbook([single_payload], tmp_path / "out.xlsx"))["Cycles"][1]]

    assert list(frame.columns) == [h for h in headers if h]
    assert len(frame) == len(single_payload["cycles"])


def test_csv_can_carry_a_specimen_column(tmp_path, payloads):
    frame = cycles_dataframe(payloads, with_specimen=True)
    assert frame.columns[0] == "Specimen"
    assert len(frame) == sum(len(p["cycles"]) for p in payloads)


# ----------------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------------


def test_html_is_self_contained(single_payload):
    page = render([single_payload])
    assert page.startswith("<!doctype html>")
    assert "<style>" in page
    for external in ("http://", "https://", "<script"):
        assert external not in page


def test_html_warns_about_multi_stage_and_missing_h0(payloads, single_payload):
    multi = render(payloads)
    assert "Multi-stage test" in multi
    assert "not comparable across stages" in multi

    bare = render([single_payload])
    assert "strain-normalised columns are omitted" in bare


def test_html_surfaces_loader_notes(single_payload):
    page = render([single_payload])
    assert "displacement channels found" in page


def test_html_escapes_content(single_payload):
    hostile = dict(single_payload)
    hostile["specimen"] = {**single_payload["specimen"], "label": "<img src=x onerror=1>"}
    page = render([hostile])

    assert "<img src=x" not in page
    assert "&lt;img src=x" in page


# ----------------------------------------------------------------------------
# Files written by the pipeline
# ----------------------------------------------------------------------------


def test_every_artifact_is_written(workspace, series_file):
    result = ingest([series_file], workspace, material="PEEK")

    for specimen in result.specimens:
        for path in (specimen.json_path, specimen.csv_path,
                     specimen.xlsx_path, specimen.html_path):
            assert path.exists() and path.stat().st_size > 0

    # Two specimens in the export, so a combined run workbook is written too.
    assert result.run_xlsx is not None and result.run_xlsx.exists()
    assert result.run_html is not None and result.run_html.exists()
    assert result.run_xlsx.with_suffix(".csv").exists()


def test_single_specimen_run_does_not_duplicate_the_workbook(workspace, single_file):
    result = ingest([single_file], workspace, material="TALCO50")
    assert result.run_xlsx == result.specimens[0].xlsx_path
    assert len(list(result.run_dir.glob("*.xlsx"))) == 1
