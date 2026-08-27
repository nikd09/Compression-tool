"""
logging_config.py: the one place handlers get attached to the
`compression_tool` logger tree.

Its defining property has to be idempotency -- app.py's main() runs this
on every single Streamlit rerun of every session, and a duplicate handler
on each of those would duplicate every log line forever, plus (for the
file handler) leak file descriptors against a file that also rotates.
"""

from __future__ import annotations

import logging

from compression_tool.logging_config import configure_logging


def _reset_logger():
    logger = logging.getLogger("compression_tool")
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    return logger


def test_stream_handler_always_attached():
    _reset_logger()
    logger = configure_logging()
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0], logging.StreamHandler)
    _reset_logger()


def test_file_handler_added_when_log_dir_given(tmp_path):
    _reset_logger()
    logger = configure_logging(tmp_path / "logs")
    assert len(logger.handlers) == 2
    assert (tmp_path / "logs").is_dir()
    _reset_logger()


def test_repeated_calls_do_not_duplicate_handlers(tmp_path):
    """The exact property app.py's every-rerun call depends on."""
    _reset_logger()
    configure_logging(tmp_path / "logs")
    logger = configure_logging(tmp_path / "logs")
    assert len(logger.handlers) == 2
    _reset_logger()


def test_a_log_line_actually_reaches_the_file(tmp_path):
    _reset_logger()
    configure_logging(tmp_path / "logs")
    logging.getLogger("compression_tool.pipeline").info("hello from a test")
    for h in logging.getLogger("compression_tool").handlers:
        h.flush()
    content = (tmp_path / "logs" / "compression_tool.log").read_text(encoding="utf-8")
    assert "hello from a test" in content
    _reset_logger()
