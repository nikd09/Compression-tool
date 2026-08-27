"""
logging_config.py
==================
One function, called once, from an entry point only (`webapp/app.py`'s
`main()`, `cli.py`'s `main()`) -- never from a library module. Every
library module below this just does `logging.getLogger(__name__)` and
calls `.info()`/`.warning()`/`.exception()` on it; where those records
actually GO (stderr, a file, both, at what level) is a decision for
whoever is running the tool, made here, once, not baked into the modules
that emit them.

Logs to a per-machine local folder, not under the workspace -- the same
reasoning `persistence.default_index_root()` already documents for
`knowledge_base.db`: `root` is expected to be a synced or shared folder,
and a log file that is rotated (truncated and re-opened) while a sync
client or a second machine might be reading it is the same class of risk
syncing a live SQLite file already is. audit.py's one-file-per-event
records ARE safe under a shared root for the same reason THIS is not --
each is written once, atomically, and never touched again.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

_LOGGER_NAME = "compression_tool"
_MAX_BYTES = 2_000_000
_BACKUP_COUNT = 5


def configure_logging(
    log_dir: Optional[Path] = None, *, level: int = logging.INFO
) -> logging.Logger:
    """Configure the `compression_tool` logger tree once and return it.

    Idempotent on purpose: a Streamlit script re-runs `app.py`'s `main()`
    on every rerun of every session, and adding a duplicate handler on each
    of those would duplicate every log line (and, for the file handler,
    hold multiple open file descriptors against a file that also rotates
    under them). Checking for existing handlers is what stays correct
    across every rerun without needing a separate "have I already done
    this" flag threaded through the caller.

    `log_dir=None` (the CLI's default -- nothing about where the CLI is
    invoked from is reliably a good place for a growing file) logs to
    stderr only, which is exactly what a terminal session already shows
    and what run_webapp.bat's window already captures.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "compression_tool.log",
            maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger
