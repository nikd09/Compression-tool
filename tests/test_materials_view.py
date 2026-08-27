"""
materials_view.py: the Rename/Delete buttons on a material card are gated by
permissions.is_admin(ws) at the VIEW layer -- permissions.py's own tests
(test_permissions.py) already pin what is_admin() returns for a given
admins.json; these pin that materials_view.render() actually acts on that
return value and hides the buttons rather than just computing it and
ignoring the result.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from compression_tool import Workspace, ingest, permissions


def _app() -> None:
    import os

    from compression_tool.persistence import Workspace
    from compression_tool.webapp import materials_view

    ws = Workspace.at(os.environ["_CT_TEST_WORKSPACE_ROOT"])
    materials_view.render(ws)


def _run(monkeypatch, workspace) -> AppTest:
    monkeypatch.setenv("_CT_TEST_WORKSPACE_ROOT", str(workspace))
    return AppTest.from_function(_app).run()


def _button_keys(at) -> set[str]:
    return {b.key for b in at.button if b.key}


def test_manage_buttons_show_when_admins_json_does_not_exist(monkeypatch, workspace, single_file):
    """Unrestricted until someone claims admin access -- see permissions.py's
    module docstring: everyone can manage until admins.json exists."""
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")

    at = _run(monkeypatch, workspace)
    assert not at.exception
    keys = _button_keys(at)
    assert any(k.startswith("rename_material_") for k in keys)
    assert any(k.startswith("delete_material_") for k in keys)


def test_manage_buttons_hidden_for_a_non_admin(monkeypatch, workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")
    permissions.add_admin(ws, "someone-else-entirely")

    at = _run(monkeypatch, workspace)
    assert not at.exception
    keys = _button_keys(at)
    assert not any(k.startswith("rename_material_") for k in keys)
    assert not any(k.startswith("delete_material_") for k in keys)


def test_manage_buttons_show_for_a_listed_admin(monkeypatch, workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")
    permissions.add_admin(ws, permissions.current_user())

    at = _run(monkeypatch, workspace)
    assert not at.exception
    keys = _button_keys(at)
    assert any(k.startswith("rename_material_") for k in keys)
    assert any(k.startswith("delete_material_") for k in keys)
