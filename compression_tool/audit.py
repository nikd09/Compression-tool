"""
audit.py
========
Who ingested what, and when.

One small JSON file per ingest call, under `<workspace>/audit/` -- never a
single growing log file that every ingester on a shared drive would have to
append to. A shared, appended-to log is exactly the write pattern this
codebase avoids everywhere else (see persistence.write_json's docstring):
two ingesters appending around the same moment can interleave their writes
into a corrupt line, or worse, an entire lost record, depending on how the
underlying filesystem buffers a write that is not a whole new file. One
brand-new, atomically-written file per ingest sidesteps that entirely -- each
record is only ever written by the one process that created it.

Best-effort and disposable, like the SQLite index and reports/: nothing
downstream reads an audit record to reconstruct state, so a write that fails
(a read-only share, a permissions problem, a full disk) is swallowed rather
than allowed to fail an ingest that otherwise succeeded and was already
written to disk in full.
"""

from __future__ import annotations

import getpass
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .persistence import Workspace, read_json, write_json

_DIRNAME = "audit"


def record_ingest(
    ws: Workspace,
    *,
    material: str,
    run_dir: Path,
    sources: Iterable[dict],
    specimens: Iterable[dict],
    skipped: Iterable[tuple[str, str]],
) -> Optional[Path]:
    """Write one audit record for an ingest call and return its path, or
    None if the record could not be written -- never raises, so an ingest
    that already succeeded is never reported as failed over this."""
    try:
        when = datetime.now(timezone.utc)
        entry = {
            "timestamp_utc": when.isoformat(timespec="seconds"),
            "user": _current_user(),
            "host": _current_host(),
            "material": material,
            "run_dir": ws.relative(run_dir),
            "sources": [
                {"source_file": s.get("source_file"), "sha256": s.get("sha256")}
                for s in sources
            ],
            "specimens": [s.get("label") for s in specimens],
            "skipped": [{"name": name, "why": why} for name, why in skipped],
        }
        stamp = when.strftime("%Y%m%dT%H%M%SZ")
        path = ws.root / _DIRNAME / f"{stamp}_{uuid.uuid4().hex[:8]}.json"
        write_json(entry, path)
        return path
    except Exception:  # noqa: BLE001 - an ingest that wrote every specimen
        # and updated the index must not be reported as failed because a
        # nice-to-have audit trail, written last and read by nobody
        # downstream, could not be.
        return None


def _current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name resolvable is not a
        # reason to lose the rest of the record.
        return "unknown"


def _current_host() -> str:
    try:
        return socket.gethostname()
    except OSError:
        return "unknown"


def list_entries(ws: Workspace, limit: Optional[int] = None) -> list[dict]:
    """Every audit record, newest first. Records are written atomically
    (via write_json), so a genuinely half-written one should not exist; the
    per-file try/except is defensive against a foreign or hand-edited file
    landing in the same directory -- one that fails to parse is skipped
    rather than failing the whole listing.

    Ordered by file modification time, not by the timestamp embedded in the
    filename: the filename's timestamp is second-resolution, so two ingests
    a fraction of a second apart can share one, and a plain filename sort
    then breaks the tie on the trailing random id -- unrelated to which was
    actually written first. mtime does not have that ambiguity.
    """
    audit_dir = ws.root / _DIRNAME
    if not audit_dir.exists():
        return []
    paths = sorted(
        audit_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    entries = []
    for path in paths:
        try:
            entries.append(read_json(path))
        except (OSError, ValueError):
            continue
        if limit is not None and len(entries) >= limit:
            break
    return entries
