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


def test_material_picker_honours_a_previously_active_material(monkeypatch, workspace, single_file):
    """Bound to the same "active_material" session key materials_view.py
    writes when a card is clicked -- opening a material there and switching
    to Results should land on it, not the alphabetically-first material."""
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="ALPHA")
    ingest([single_file], ws, material="BETA")

    monkeypatch.setenv("_CT_TEST_WORKSPACE_ROOT", str(workspace))
    at = AppTest.from_function(_app)
    at.session_state["active_material"] = "BETA"
    at.run()

    assert not at.exception
    assert at.selectbox[0].value == "BETA"


def test_a_stale_active_material_falls_back_to_the_first_option(monkeypatch, workspace, single_file):
    """A material name carried over from a different workspace, or one that
    was since renamed/deleted, must not make the picker reject every
    option outright -- see the guard in results_view.py before `key=`."""
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="ALPHA")

    monkeypatch.setenv("_CT_TEST_WORKSPACE_ROOT", str(workspace))
    at = AppTest.from_function(_app)
    at.session_state["active_material"] = "Some material that no longer exists"
    at.run()

    assert not at.exception
    assert at.selectbox[0].value == "ALPHA"
