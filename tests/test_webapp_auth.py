"""
webapp/auth.py: the optional shared-password gate.

Not real authentication (see the module docstring) -- these tests pin the
one property that actually matters: unset, it never blocks anyone (every
local/dev launch today has never set it); set, only the right password
gets past `st.stop()`, and a wrong one shows an error rather than letting
the rest of the script run.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _app() -> None:
    # AppTest.from_function() re-execs this function's own source in an
    # isolated namespace -- it does not carry over this test module's
    # top-level imports, so both imports have to live in here.
    import streamlit as st

    from compression_tool.webapp.auth import require_password

    require_password()
    st.text("past the gate")


def test_unset_password_never_blocks(monkeypatch):
    monkeypatch.delenv("COMPRESSION_TOOL_PASSWORD", raising=False)
    at = AppTest.from_function(_app).run()
    assert not at.exception
    assert at.text[0].value == "past the gate"


def test_set_password_blocks_until_entered(monkeypatch):
    monkeypatch.setenv("COMPRESSION_TOOL_PASSWORD", "correct-horse")
    at = AppTest.from_function(_app).run()
    assert not at.exception
    assert not at.text  # st.stop() ran before "past the gate" -- nothing after it rendered
    assert at.text_input(key="_ct_password_attempt")


def test_wrong_password_shows_an_error_and_still_blocks(monkeypatch):
    monkeypatch.setenv("COMPRESSION_TOOL_PASSWORD", "correct-horse")
    at = AppTest.from_function(_app).run()
    at.text_input(key="_ct_password_attempt").set_value("wrong-guess")
    at.button[0].click().run()
    assert not at.text
    assert at.error
    assert "Incorrect" in at.error[0].value


def test_right_password_gets_through(monkeypatch):
    monkeypatch.setenv("COMPRESSION_TOOL_PASSWORD", "correct-horse")
    at = AppTest.from_function(_app).run()
    at.text_input(key="_ct_password_attempt").set_value("correct-horse")
    at.button[0].click().run()
    assert not at.exception
    assert at.text[0].value == "past the gate"
