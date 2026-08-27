"""
compare_view.py's per-group material filter -- narrows a group's specimen
picker to one material at a time, instead of always listing every specimen
in the workspace flat. Exercised through AppTest (not a pure function): the
whole point is what the SELECTBOX does to the MULTISELECT next to it, which
is genuinely UI wiring, not something to fake with a plain function call.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from compression_tool import Workspace, ingest


def _app() -> None:
    # AppTest.from_function() re-execs this function's own source in an
    # isolated namespace -- see test_webapp_auth.py's _app() for the same
    # note. The workspace path is passed in via an env var (set by the test,
    # in the same process AppTest runs in) since this function takes no
    # arguments of its own.
    import os

    from compression_tool.persistence import Workspace
    from compression_tool.webapp import compare_view

    ws = Workspace.at(os.environ["_CT_TEST_WORKSPACE_ROOT"])
    compare_view.render(ws)


def _run(monkeypatch, workspace) -> AppTest:
    monkeypatch.setenv("_CT_TEST_WORKSPACE_ROOT", str(workspace))
    return AppTest.from_function(_app).run()


def test_group_defaults_to_one_materials_specimens(monkeypatch, workspace, single_file, series_file):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="MatA")
    ingest([series_file], ws, material="MatB")

    at = _run(monkeypatch, workspace)
    assert not at.exception

    filter0 = at.selectbox(key="cmp_material_filter_0")
    assert filter0.value == "MatA"
    specimens0 = at.multiselect(key="cmp_specimens_0_MatA")
    assert len(specimens0.value) == 1
    assert len(specimens0.options) == 1


def test_switching_the_filter_repopulates_with_the_new_materials_specimens(
    monkeypatch, workspace, single_file, series_file
):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="MatA")
    ingest([series_file], ws, material="MatB")

    at = _run(monkeypatch, workspace)
    at.selectbox(key="cmp_material_filter_0").set_value("MatB").run()

    # The old key's widget is gone; the new one starts pre-filled with
    # MatB's two specimens, not still carrying MatA's single pick.
    specimens0 = at.multiselect(key="cmp_specimens_0_MatB")
    assert len(specimens0.value) == 2
    assert len(specimens0.options) == 2


def test_all_materials_starts_the_group_empty_not_the_whole_workspace(
    monkeypatch, workspace, single_file, series_file
):
    """The scaling fix this exists for: a workspace with many materials must
    not dump every specimen into a group's multiselect by default just
    because 'All materials' was picked -- that is exactly the flat,
    unscoped list the material filter is meant to replace."""
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="MatA")
    ingest([series_file], ws, material="MatB")

    at = _run(monkeypatch, workspace)
    at.selectbox(key="cmp_material_filter_0").set_value("All materials").run()

    specimens0 = at.multiselect(key="cmp_specimens_0_All materials")
    assert specimens0.value == []
    assert len(specimens0.options) == 3
