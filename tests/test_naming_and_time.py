"""Short specimen names, readable local times and the Compare workbook."""

from __future__ import annotations

import io

import pandas as pd
from openpyxl import load_workbook

from compression_tool.compare_export import compare_workbook
from compression_tool.naming import short_name, short_names
from compression_tool.timefmt import local_time


def test_short_name_drops_the_repeated_words_and_keeps_the_specimen():
    assert short_name("Demo-A compression test_S1") == "Demo-A · S1"
    assert short_name("Mehrstufiger_Druckversuch_PU-Schaum_200kg_S3") == "PU-Schaum 200kg · S3"
    assert short_name("0574409bed95_Mat-1-compression-test_S2") == "Mat-1 · S2"
    assert short_name("Mehrstufiger Druckversuch_S1") == "S1"
    assert short_name("Probe 7") == "Probe 7"


def test_short_name_cuts_very_long_file_parts():
    name = short_name("Sehr_langer_Dateiname_mit_vielen_Angaben_2026-11-03_S2")
    assert name.endswith("… · S2")
    assert len(name) <= 28 + len(" · S2")


def test_short_names_never_show_two_different_specimens_alike():
    assert short_names(["A_S1", "A S1", "A_S2"]) == ["A · S1", "A · S1 (2)", "A · S2"]


def test_local_time_is_readable_and_leaves_other_text_alone():
    shown = local_time("2026-11-26T10:04:21+00:00")
    assert len(shown) == len("26.11.2026 11:04") and shown[2] == "." and shown[5] == "."
    assert local_time("2026-11-26T10:04:21+00:00", date_only=True).endswith(".2026")
    assert local_time("not a time") == "not a time"
    assert local_time(None) == "-"


def test_compare_workbook_gives_mean_sd_and_n_per_group_and_step():
    df = pd.DataFrame({
        "specimen_id": ["a", "a", "b", "b", "c"],
        "Cycle": [1, 2, 1, 2, 1],
        "StressLevel_MPa": [50.0, 100.0, 50.0, 100.0, 50.0],
        "Stiffness_common_MPa_per_mm": [100.0, 110.0, 120.0, 130.0, 200.0],
        "MaxDisp_mm": [0.1, 0.2, 0.1, 0.2, 0.3],
    })
    groups = [{"name": "G1", "ids": ["a", "b"]}, {"name": "G2", "ids": ["c"]}]
    data = compare_workbook(df, groups, {"a": "M1 compression test_S1", "b": "M1 compression test_S2",
                                         "c": "M2_S1"}, {"a": "M1", "b": "M1", "c": "M2"})
    book = load_workbook(io.BytesIO(data))
    assert book.sheetnames == ["Groups", "Stiffness common", "Max displacement"]

    groups_sheet = [r for r in book["Groups"].iter_rows(min_row=5, values_only=True)]
    assert groups_sheet[0][:4] == ("G1", "M1", "S1", "M1 compression test_S1")

    rows = list(book["Stiffness common"].iter_rows(min_row=3, values_only=True))
    assert rows[0][:7] == ("Stress step (MPa)", "G1 mean", "G1 SD", "G1 n", "G2 mean", "G2 SD", "G2 n")
    assert rows[1][:7] == (50, 110, 10, 2, 200, 0, 1)
    assert rows[2][:4] == (100, 120, 10, 2)
    assert rows[2][4] is None  # G2 never reached 100 MPa


def test_short_name_leaves_out_the_material_and_mehrstufen():
    label = "Druckversuch Mehrstufen_TALC050AN1_S1"
    assert short_name(label, "TALC050AN1") == "S1"
    assert short_name(label) == "TALC050AN1 · S1"
    # Only the whole material name is removed, never part of a longer one.
    assert short_name("Mat-10 compression test_S2", "Mat-1") == "Mat-10 · S2"
    # A second file of the same material keeps what tells it apart.
    assert short_name("Druckversuch Mehrstufen_TALC050AN1_2_S1", "TALC050AN1") == "2 · S1"
