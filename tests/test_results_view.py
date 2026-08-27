"""
results_view.py's degraded path: the index still lists a specimen whose
record was removed straight from disk (Explorer, the shared drive) rather
than through the app -- the tab has to skip it and point at Config's
"Reindex from disk", not crash. Exercised through AppTest since this is
about what actually renders, not just what read_json() raises.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from compression_tool import Workspace, ingest


def _app() -> None:
    import os

    from compression_tool.persistence import Workspace
    from compression_tool.webapp import results_view

    ws = Workspace.at(os.environ["_CT_TEST_WORKSPACE_ROOT"])
    results_view.render(ws)


def _run(monkeypatch, workspace) -> AppTest:
    monkeypatch.setenv("_CT_TEST_WORKSPACE_ROOT", str(workspace))
    return AppTest.from_function(_app).run()


def test_healthy_workspace_renders_without_exception(monkeypatch, workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")

    at = _run(monkeypatch, workspace)
    assert not at.exception
    assert not at.error


def test_a_specimen_deleted_outside_the_app_shows_a_reindex_error_not_a_crash(
    monkeypatch, workspace, single_file
):
    ws = Workspace.at(workspace).ensure()
    result = ingest([single_file], ws, material="TALCO50")
    result.specimens[0].json_path.unlink()

    at = _run(monkeypatch, workspace)
    assert not at.exception
    assert at.error
    assert "Reindex from disk" in at.error[0].value
