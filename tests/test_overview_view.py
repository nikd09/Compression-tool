"""
overview_view.py: the landing view. Empty-workspace state links to Ingest;
a populated workspace shows the three headline counts and rolls up any
diagnostic warning among the most recently analysed specimens without
requiring a visit to that material's own Results tab.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from compression_tool import Workspace, ingest


def _app() -> None:
    import os

    from compression_tool.persistence import Workspace
    from compression_tool.webapp import overview_view

    ws = Workspace.at(os.environ["_CT_TEST_WORKSPACE_ROOT"])
    overview_view.render(ws)


def _run(monkeypatch, workspace) -> AppTest:
    monkeypatch.setenv("_CT_TEST_WORKSPACE_ROOT", str(workspace))
    return AppTest.from_function(_app).run()


def test_empty_workspace_shows_a_way_into_ingest(monkeypatch, workspace):
    at = _run(monkeypatch, workspace)
    assert not at.exception
    assert any(b.label == "Go to Ingest" for b in at.button)


def test_going_to_ingest_from_the_empty_state_switches_the_nav(monkeypatch, workspace):
    at = _run(monkeypatch, workspace)
    btn = next(b for b in at.button if b.label == "Go to Ingest")
    at = btn.click().run()
    assert at.session_state["nav_view"] == "Ingest"


def test_a_populated_workspace_shows_the_headline_counts(monkeypatch, workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")

    at = _run(monkeypatch, workspace)

    assert not at.exception
    values = {m.label: m.value for m in at.metric}
    assert values["Materials"] == "1"
    assert values["Specimens"] == "1"
    assert values["Runs"] == "1"


def test_a_freshly_ingested_run_surfaces_its_diagnostic_warning(monkeypatch, workspace, single_file):
    """single_file's synthetic signal always trips at least one diagnostic
    (the synthetic export carries no h0, and its low-stress lead-in run gets
    discarded by the peak filter) -- the point being pinned here is that the
    Overview panel actually surfaces one, by name, without requiring a visit
    to that material's own Results tab, not which exact warning fires."""
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")

    at = _run(monkeypatch, workspace)

    assert not at.exception
    rendered = " ".join(m.value for m in at.markdown)
    assert "Needs a look" in rendered
    assert "TALCO50" in rendered
    captions = [c.value for c in at.caption]
    assert not any("No open diagnostic warnings" in c for c in captions)


