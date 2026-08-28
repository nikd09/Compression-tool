"""
materials_view.py: the Rename/Delete buttons on a material card.

Visible to every visitor regardless of admin status (a hidden feature is a
feature nobody but an admin even knows exists); permissions.is_admin(ws) is
instead checked at CLICK time, showing an error instead of opening the
Rename/Delete dialog for anyone not on the admins.json list. permissions.py's
own tests (test_permissions.py) already pin what is_admin() returns for a
given admins.json; these pin that materials_view.render() actually acts on
that return value at the right moment, not just computes it and ignores the
result.

Also covers the Download button: available to everyone unconditionally
(a read, not a write), only once the combined dashboard already exists on
disk.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from compression_tool import Workspace, export_material, ingest, permissions


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


def test_manage_buttons_are_visible_with_no_admin_restriction(monkeypatch, workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")

    at = _run(monkeypatch, workspace)
    assert not at.exception
    keys = _button_keys(at)
    assert any(k.startswith("rename_material_") for k in keys)
    assert any(k.startswith("delete_material_") for k in keys)


def test_manage_buttons_stay_visible_for_a_non_admin(monkeypatch, workspace, single_file):
    """The behaviour this pins changed on purpose: the buttons used to be
    hidden entirely for a non-admin. Now they stay visible -- see the
    module docstring -- and it is the CLICK that is gated instead."""
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")
    permissions.add_admin(ws, "someone-else-entirely")

    at = _run(monkeypatch, workspace)
    assert not at.exception
    keys = _button_keys(at)
    assert any(k.startswith("rename_material_") for k in keys)
    assert any(k.startswith("delete_material_") for k in keys)


def test_clicking_rename_as_a_non_admin_shows_an_error_not_the_dialog(
    monkeypatch, workspace, single_file
):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")
    permissions.add_admin(ws, "someone-else-entirely")

    at = _run(monkeypatch, workspace)
    rename_btn = next(b for b in at.button if b.key and b.key.startswith("rename_material_"))
    rename_btn.click().run()

    assert not at.exception
    assert at.error
    assert "Only an admin" in at.error[0].value
    assert not any(ti.label == "New name" for ti in at.text_input)


def test_clicking_delete_as_a_non_admin_shows_an_error_not_the_dialog(
    monkeypatch, workspace, single_file
):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")
    permissions.add_admin(ws, "someone-else-entirely")

    at = _run(monkeypatch, workspace)
    delete_btn = next(b for b in at.button if b.key and b.key.startswith("delete_material_"))
    delete_btn.click().run()

    assert not at.exception
    assert at.error
    assert "Only an admin" in at.error[0].value


def test_clicking_rename_as_an_admin_opens_the_dialog(monkeypatch, workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")
    permissions.add_admin(ws, permissions.current_user())

    at = _run(monkeypatch, workspace)
    rename_btn = next(b for b in at.button if b.key and b.key.startswith("rename_material_"))
    rename_btn.click().run()

    assert not at.exception
    assert not at.error
    assert any(ti.label == "New name" for ti in at.text_input)


def test_download_button_available_once_the_dashboard_exists(monkeypatch, workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")
    export_material(ws, "TALCO50")  # ingest() already builds this; explicit for clarity

    at = _run(monkeypatch, workspace)
    assert not at.exception
    download_keys = {b.key for b in at.download_button if b.key}
    assert any(k.startswith("download_material_") for k in download_keys)


def test_no_download_button_before_the_dashboard_is_built(monkeypatch, workspace, single_file):
    """Deliberately does NOT call export_material() first -- ingest() always
    builds it automatically today, so this pins the fallback caption for
    the (currently hypothetical, but cheap to guarantee) case where it
    hasn't been, rather than the Download button silently reading a file
    that is not there."""
    from compression_tool.persistence import slugify

    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")
    (ws.root / "reports" / f"{slugify('TALCO50')}.html").unlink()

    at = _run(monkeypatch, workspace)
    assert not at.exception
    download_keys = {b.key for b in at.download_button if b.key}
    assert not any(k.startswith("download_material_") for k in download_keys)
    assert any("not built yet" in c.value for c in at.caption)
