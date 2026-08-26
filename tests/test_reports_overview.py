"""
reports_overview.py: the all-materials overview page.

Its job is to let someone who only wants to browse and compare materials
never need the live app -- these tests exist to pin that it actually
covers every material, links land on the right per-material report, and a
workspace with nothing in it yet does not produce a broken or misleading
page.
"""

from __future__ import annotations

import json
import re

import pytest

from compression_tool import Workspace, build_overview, ingest
from compression_tool.reports_overview import material_rows


def _embedded_data(html: str) -> dict:
    match = re.search(r"const DATA = (\{.*\});", html)
    assert match, "DATA placeholder was not replaced"
    return json.loads(match.group(1))


def test_no_specimens_returns_none_and_writes_nothing(workspace):
    ws = Workspace.at(workspace).ensure()
    result = build_overview(ws)
    assert result is None
    assert not (ws.root / "reports" / "_Overview.html").exists()


def test_covers_every_material_across_separate_ingest_sessions(
    workspace, series_file, single_file
):
    ingest([series_file], workspace, material="PEEK")
    ingest([single_file], workspace, material="TALCO50")
    ws = Workspace.at(workspace)

    path = build_overview(ws)
    assert path is not None and path.exists()
    assert path.name == "_Overview.html"
    assert path.parent == ws.root / "reports"

    data = _embedded_data(path.read_text(encoding="utf-8"))
    names = {m["material"] for m in data["materials"]}
    assert names == {"PEEK", "TALCO50"}


def test_material_stats_are_correct(workspace, series_file):
    """series_file has 2 specimens sharing one run; every summary number on
    the overview must reflect that, not double-count or drop one."""
    ingest([series_file], workspace, material="PEEK")
    ws = Workspace.at(workspace)

    path = build_overview(ws)
    data = _embedded_data(path.read_text(encoding="utf-8"))
    (entry,) = data["materials"]
    assert entry["material"] == "PEEK"
    assert entry["specimens"] == 2
    assert entry["runs"] == 1
    assert entry["meanPeak"] is not None and entry["meanPeak"] > 0
    assert entry["meanH0"] == pytest.approx(0.471)
    assert entry["dateAdded"]


def test_date_added_is_the_earliest_ingest_not_the_latest(workspace, single_file, series_file):
    """A material's 'date added' is when it first showed up in this
    workspace, not its most recent activity -- ingesting it again later
    must not move that date forward."""
    a = ingest([single_file], workspace, material="PEEK")
    b = ingest([series_file], workspace, material="PEEK")
    ws = a.workspace

    expected_earliest = min(
        a.specimens[0].payload["created_utc"],
        b.specimens[0].payload["created_utc"],
    )
    (row,) = material_rows(ws)
    assert row["dateAdded"] == expected_earliest


def test_material_rows_is_empty_for_a_workspace_with_nothing_ingested(workspace):
    ws = Workspace.at(workspace).ensure()
    assert material_rows(ws) == []


def test_link_slug_matches_the_material_export_filename(workspace, series_file):
    """The overview's links have to land on exactly the file
    material_export.py writes -- same slugify(), same place."""
    ingest([series_file], workspace, material="PEEK-GF30")
    ws = Workspace.at(workspace)

    path = build_overview(ws)
    data = _embedded_data(path.read_text(encoding="utf-8"))
    (entry,) = data["materials"]
    assert entry["slug"] == "PEEK-GF30"
    assert (ws.root / "reports" / f"{entry['slug']}.html").exists()


def test_ingest_regenerates_the_overview_automatically(workspace, series_file, single_file):
    """No caller has to remember to call build_overview separately."""
    result_a = ingest([series_file], workspace, material="PEEK")
    assert result_a.overview_html is not None

    data_a = _embedded_data(result_a.overview_html.read_text(encoding="utf-8"))
    assert {m["material"] for m in data_a["materials"]} == {"PEEK"}

    result_b = ingest([single_file], workspace, material="TALCO50")
    data_b = _embedded_data(result_b.overview_html.read_text(encoding="utf-8"))
    assert {m["material"] for m in data_b["materials"]} == {"PEEK", "TALCO50"}
