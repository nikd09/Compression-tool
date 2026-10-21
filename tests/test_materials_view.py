"""
materials_view.py: the Rename/Delete buttons on a material card.

Open to every user (the admin gate was removed deliberately: the project
must keep working without any one person around). Delete still needs the
material's exact name typed to confirm.

Also covers the Download button: available to everyone unconditionally
(a read, not a write), only once the combined dashboard already exists on
disk.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from compression_tool import Workspace, export_material, ingest


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


def test_manage_buttons_are_visible(monkeypatch, workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")

    at = _run(monkeypatch, workspace)
    assert not at.exception
    keys = _button_keys(at)
    assert any(k.startswith("rename_material_") for k in keys)
    assert any(k.startswith("delete_material_") for k in keys)


def test_clicking_rename_opens_the_dialog_for_everyone(monkeypatch, workspace, single_file):
    """There is no admin gate any more: any user may rename a material."""
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")

    at = _run(monkeypatch, workspace)
    rename_btn = next(b for b in at.button if b.key and b.key.startswith("rename_material_"))
    rename_btn.click().run()

    assert not at.exception
    assert not at.error
    assert any(ti.label == "New name" for ti in at.text_input)


def test_clicking_delete_opens_the_type_the_name_dialog_for_everyone(
    monkeypatch, workspace, single_file
):
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")

    at = _run(monkeypatch, workspace)
    delete_btn = next(b for b in at.button if b.key and b.key.startswith("delete_material_"))
    delete_btn.click().run()

    assert not at.exception
    assert any("Type" in (ti.label or "") for ti in at.text_input)


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


def test_opening_a_material_card_sets_the_shared_active_material(monkeypatch, workspace, single_file):
    """Results' Material picker is bound to this same key -- clicking a card
    here is meant to carry the choice over there, not just open this tab's
    own dashboard view. See results_view.py."""
    ws = Workspace.at(workspace).ensure()
    ingest([single_file], ws, material="TALCO50")

    at = _run(monkeypatch, workspace)
    open_btn = next(b for b in at.button if b.key and b.key.startswith("open_material_"))
    at = open_btn.click().run()

    assert not at.exception
    assert at.session_state["active_material"] == "TALCO50"
