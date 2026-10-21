"""
user_settings.py + the Library switcher in webapp/common.workspace_picker():
two libraries (Shared / My local), per-user paths remembered on this PC.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from compression_tool import user_settings


@pytest.fixture
def settings_home(tmp_path, monkeypatch):
    """Point the per-user settings folder at a temp dir for the test."""
    home = tmp_path / "localappdata"
    monkeypatch.setenv("LOCALAPPDATA", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home))
    monkeypatch.delenv("COMPRESSION_TOOL_WORKSPACE", raising=False)
    monkeypatch.delenv("COMPRESSION_TOOL_ALLOWED_ROOTS", raising=False)
    return home


def test_defaults_when_nothing_saved(settings_home, monkeypatch):
    monkeypatch.setenv("COMPRESSION_TOOL_WORKSPACE", "/team/library")
    s = user_settings.load()
    assert s["shared_path"] == "/team/library"
    assert s["local_path"].endswith("CompressLab data")
    assert s["active"] == user_settings.SHARED


def test_default_shared_is_outside_the_tool_folder(settings_home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # stands in for the tool folder
    assert user_settings.load()["shared_path"].endswith("CompressLab shared library")


def test_existing_data_folder_is_still_used(settings_home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "Records").mkdir(parents=True)
    assert user_settings.load()["shared_path"] == str((tmp_path / "data").resolve())


def test_save_then_load_round_trips(settings_home):
    assert user_settings.save(
        {"shared_path": "/a", "local_path": "/b", "active": user_settings.LOCAL}
    )
    s = user_settings.load()
    assert (s["shared_path"], s["local_path"], s["active"]) == ("/a", "/b", "local")


def test_corrupt_settings_file_falls_back_to_defaults(settings_home):
    path = user_settings.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert user_settings.load()["active"] == user_settings.SHARED


def test_bad_values_are_ignored(settings_home):
    path = user_settings.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text('{"shared_path": 5, "local_path": "  ", "active": "nope"}', encoding="utf-8")
    s = user_settings.load()
    assert s["active"] == user_settings.SHARED
    assert s["local_path"].endswith("CompressLab data")


def _app() -> None:
    from compression_tool.webapp.common import workspace_picker

    ws = workspace_picker()
    import streamlit as st

    st.write(f"ROOT={ws.root.resolve()}")


def test_switcher_uses_shared_by_default_and_switches_to_local(settings_home, tmp_path):
    shared, local = tmp_path / "shared", tmp_path / "mylocal"
    user_settings.save(
        {"shared_path": str(shared), "local_path": str(local), "active": user_settings.SHARED}
    )
    at = AppTest.from_function(_app).run()
    assert not at.exception
    assert any(f"ROOT={shared.resolve()}" in m.value for m in at.markdown)

    at.selectbox(key="library_active_en").set_value(user_settings.LOCAL).run()
    assert not at.exception
    assert any(f"ROOT={local.resolve()}" in m.value for m in at.markdown)
    # The choice is remembered for next launch.
    assert user_settings.load()["active"] == user_settings.LOCAL


def test_switching_language_keeps_the_library_and_relabels_it(settings_home, tmp_path):
    user_settings.save({"shared_path": str(tmp_path / "s"), "local_path": str(tmp_path / "l"),
                        "active": user_settings.SHARED})
    at = AppTest.from_function(_app).run()
    at.selectbox(key="library_active_en").set_value(user_settings.LOCAL).run()
    at.session_state["app_lang"] = "de"
    at.run()
    assert not at.exception
    box = at.selectbox(key="library_active_de")
    assert box.value == user_settings.LOCAL
    assert box.format_func(box.value) == "Meine lokale Bibliothek"


def test_editing_a_path_is_remembered(settings_home, tmp_path):
    at = AppTest.from_function(_app).run()
    new_local = tmp_path / "somewhere" / "else"
    at.text_input(key="library_local_path").set_value(str(new_local)).run()
    assert not at.exception
    assert user_settings.load()["local_path"] == str(new_local)


def test_allowed_roots_still_apply_to_the_active_library(settings_home, tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("COMPRESSION_TOOL_ALLOWED_ROOTS", str(allowed))
    user_settings.save(
        {"shared_path": str(tmp_path / "elsewhere"), "local_path": str(allowed / "ok"),
         "active": user_settings.SHARED}
    )
    at = AppTest.from_function(_app).run()
    assert at.error  # shared path is outside the allowed roots
    at.selectbox(key="library_active_en").set_value(user_settings.LOCAL).run()
    assert not at.error
