"""
common.config_form() -- the "Advanced: segmentation and reference
thresholds" expander shared between Ingest and Config's "Re-analyse this
run" (see test_config_view.py). Pins that it defaults to a plain Config()
and that a changed number_input actually flows into the returned Config,
not just into the widget.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from compression_tool.core import Config


def _app() -> None:
    import streamlit as st

    from compression_tool.webapp.common import config_form

    cfg = config_form(detect_holds=True)
    st.text(f"unload_frac={cfg.unload_frac}")
    st.text(f"detect_holds={cfg.detect_holds}")


def test_defaults_match_a_plain_config():
    at = AppTest.from_function(_app).run()
    assert not at.exception
    d = Config()
    assert at.text[0].value == f"unload_frac={d.unload_frac}"
    assert at.text[1].value == "detect_holds=True"


def test_a_changed_threshold_flows_into_the_returned_config():
    at = AppTest.from_function(_app).run()
    at.number_input[0].set_value(0.123).run()
    assert at.text[0].value == "unload_frac=0.123"


def test_detect_holds_is_passed_through_unchanged():
    at = AppTest.from_function(_app).run()
    assert at.text[1].value == "detect_holds=True"
