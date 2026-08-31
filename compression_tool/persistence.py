"""
persistence.py
==============
On-disk layout and the JSON record that is the system's source of truth.

    <root>/
      Raw exports/                   immutable copies of the original exports
        <sha12>_<original name>.xlsx   -- optional, ingest(archive_originals=False)
                                           skips this and records just the SHA-256
      Records/
        <material>_<YYYY-MM-DD>/
          run.json                   what was ingested, with which config
          <specimen>.json            the record -- source of truth, ALWAYS written
          <specimen>.curve.json      curve cache for the dashboard, ALWAYS written
          <specimen>.csv             per-cycle table, flat        -- optional, see below
          <specimen>.xlsx            per-cycle table + summary    -- optional, see below
          <specimen>.html            standalone report            -- optional, see below
          <material>_<date>.xlsx     all specimens of THIS RUN in one workbook -- optional
      reports/
        <material>.xlsx              every specimen ever ingested for this
        <material>.html              material, across every run -- see
                                      material_export.py. Rebuilt on every
                                      ingest; safe to delete, like the index.
      knowledge_base.db              SQLite index, rebuildable from the JSONs

    The three "optional" rows above (per-specimen and per-run csv/xlsx/html)
    are skipped by ingest(write_reports=False) -- for someone who only ever
    reads the combined reports/ workbook and dashboard and finds the per-run
    copies redundant. The JSON record and curve cache are never optional:
    everything else in this layout, including reports/, is rebuilt FROM them.

    "Raw exports" and "Records" are named for someone browsing in Explorer,
    not for a codebase -- the folders that hold most of the storage and most
    of the file count are the ones a reader is least likely to ever open.
    Workspaces created before this rename keep working: `Workspace.raw` and
    `.processed` fall back to the old `raw_input`/`processed_output` names
    when THOSE already exist on disk and the new names do not, so nothing
    already ingested has to move, and a workspace only ever writes under one
    name or the other, never a mix of both.

The JSON records are authoritative. The database is a queryable index over
them and may be deleted and rebuilt at any time; nothing is stored there that
cannot be recovered from disk.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import numpy as np
import pandas as pd

from . import diagnostics
from .core import Config, TestData
from .schema import SCHEMA_VERSION

RAW_DIRNAME = "Raw exports"
PROCESSED_DIRNAME = "Records"
DB_FILENAME = "knowledge_base.db"

# Pre-rename names. A workspace that already has one of these on disk keeps
# using it -- see Workspace.raw / .processed below -- so nothing already
# ingested under the old layout has to be moved.
_LEGACY_RAW_DIRNAME = "raw_input"
_LEGACY_PROCESSED_DIRNAME = "processed_output"


# ----------------------------------------------------------------------------
# Workspace
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Workspace:
    """The directories the tool owns. Nothing is written outside them.

    `index_root`, when set, moves ONLY the SQLite index (`db_path`) out of
    `root` -- everything else (Raw exports/, Records/, reports/)
    still lives under `root`. Exists for a workspace whose `root` is a
    synced folder (OneDrive, SharePoint) or a network share: syncing a
    SQLite file that is being actively written is a well-known corruption
    risk (the sync client and SQLite's own locking are not coordinated), and
    a network share's file locking is unreliable for SQLite regardless. The
    index is documented as disposable and rebuildable from the JSON records
    (see `knowledge_base.rebuild`), so it loses nothing by living somewhere
    that is NOT synced or shared -- typically local, per-machine storage.
    """

    root: Path
    index_root: Optional[Path] = None

    @classmethod
    def at(
        cls, root: str | os.PathLike, *, index_root: str | os.PathLike | None = None
    ) -> "Workspace":
        return cls(
            Path(root).expanduser().resolve(),
            Path(index_root).expanduser().resolve() if index_root else None,
        )

    @property
    def raw(self) -> Path:
        legacy = self.root / _LEGACY_RAW_DIRNAME
        if legacy.exists() and not (self.root / RAW_DIRNAME).exists():
            return legacy
        return self.root / RAW_DIRNAME

    @property
    def processed(self) -> Path:
        legacy = self.root / _LEGACY_PROCESSED_DIRNAME
        if legacy.exists() and not (self.root / PROCESSED_DIRNAME).exists():
            return legacy
        return self.root / PROCESSED_DIRNAME

    @property
    def db_path(self) -> Path:
        return (self.index_root or self.root) / DB_FILENAME

    def ensure(self) -> "Workspace":
        self.raw.mkdir(parents=True, exist_ok=True)
        self.processed.mkdir(parents=True, exist_ok=True)
        if self.index_root:
            self.index_root.mkdir(parents=True, exist_ok=True)
        return self

    def relative(self, path: Path) -> str:
        """Store paths relative to the workspace so a run folder stays valid
        when the whole tree is moved or shared."""
        try:
            return str(Path(path).resolve().relative_to(self.root))
        except ValueError:
            return str(Path(path).resolve())


class WorkspacePathNotAllowed(ValueError):
    """A workspace path was rejected by `check_workspace_allowed()`."""


_ALLOWED_ROOTS_ENV = "COMPRESSION_TOOL_ALLOWED_ROOTS"


def check_workspace_allowed(root: str | os.PathLike) -> None:
    """Raise `WorkspacePathNotAllowed` if `root` falls outside the roots
    named in `COMPRESSION_TOOL_ALLOWED_ROOTS` (entries joined by `os.pathsep`
    -- ';' on Windows, ':' elsewhere, the same convention PATH itself uses).

    A no-op, on purpose, when that variable is unset or empty: on a laptop
    running the app for yourself, typing a workspace path grants nothing a
    local Python process could not already do on its own -- there is no new
    capability to restrict. The moment this is hosted somewhere for someone
    ELSE to open in a browser, an unvalidated free-text path is arbitrary
    read *and* write on the server's filesystem, not just this tool's own
    data. Whoever stands up that deployment sets this variable once; callers
    that never set it keep today's behaviour exactly, unchanged.
    """
    allowed = os.environ.get(_ALLOWED_ROOTS_ENV, "").strip()
    if not allowed:
        return
    resolved = Path(root).expanduser().resolve()
    roots = [Path(p).expanduser().resolve() for p in allowed.split(os.pathsep) if p.strip()]
    for allowed_root in roots:
        if resolved == allowed_root or allowed_root in resolved.parents:
            return
    raise WorkspacePathNotAllowed(
        f"{resolved} is outside the workspace roots this deployment allows "
        f"({', '.join(str(r) for r in roots)}). Ask whoever administers it "
        f"to add this path to {_ALLOWED_ROOTS_ENV}, or point at one of the "
        f"roots already listed there."
    )


def default_index_root() -> Path:
    """Where the local SQLite index lives when a caller wants `root` to stay
    a shared/synced folder untouched by it -- see `Workspace.index_root`.

    A per-machine, per-user, NOT-synced-by-default location: %LOCALAPPDATA%
    on Windows (explicitly the "Local" app-data folder, as opposed to
    "Roaming", precisely because Local is what OneDrive Known Folder Move
    does not sync); XDG_CACHE_HOME or ~/.cache elsewhere. Callers decide
    whether to use this -- it is never applied implicitly by `Workspace.at`.
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "CompressionTool"


def workspace_index_root(root: str | os.PathLike) -> Path:
    """A per-WORKSPACE subdirectory under default_index_root() -- so two
    different workspace roots opened from the same machine never share one
    local SQLite index.

    Without this, every caller that passed `index_root=default_index_root()`
    got the exact same fixed path back regardless of `root`: opening
    workspace B, on a machine that had already built a local index for
    workspace A, would find A's index already sitting at that path and use
    it as-is -- silently showing A's materials and specimens under B's name,
    with nothing in the UI to suggest anything was wrong. Confirmed live:
    pointing the webapp at a second, unrelated workspace folder on the same
    machine did exactly that.

    The hash is what actually guarantees two different roots never collide;
    the slug in front of it is only so a human skimming the cache directory
    can tell which subfolder belongs to which workspace at a glance.
    """
    resolved = str(Path(root).expanduser().resolve())
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]
    return default_index_root() / f"{slugify(Path(root).name) or 'workspace'}-{digest}"


# ----------------------------------------------------------------------------
# Hashing and naming
# ----------------------------------------------------------------------------


def sha256_file(path: str | os.PathLike, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def config_hash(cfg: Config) -> str:
    payload = json.dumps(asdict(cfg), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def specimen_id(source_sha256: str, label: str, material: str) -> str:
    """Deterministic, so a rebuilt database reuses the same identifiers and
    references held elsewhere do not rot.

    `material` is part of the identity, not just the label and source file:
    without it, ingesting the same export under two material names produces
    two JSON records that collide on one database row (specimen_id is the
    SQLite PRIMARY KEY), and INSERT OR REPLACE silently keeps whichever was
    written or rebuilt-from-disk last -- observed as the index disagreeing
    with itself before and after a rebuild for exactly this case.
    """
    return hashlib.sha256(f"{source_sha256}:{material}:{label}".encode()).hexdigest()[:16]


_SLUG_STRIP = re.compile(r"[^A-Za-z0-9._-]+")


def slugify(text: str, fallback: str = "unnamed") -> str:
    """Filesystem-safe name that still reads like the original."""
    cleaned = _SLUG_STRIP.sub("-", str(text).strip()).strip("-._")
    return cleaned or fallback


# ----------------------------------------------------------------------------
# Raw exports -- immutable archive
# ----------------------------------------------------------------------------


def archive_raw(source: str | os.PathLike, ws: Workspace) -> tuple[Path, str]:
    """Copy an export into Raw exports/ and return (archived path, sha256).

    The copy is content-addressed and marked read-only. Re-ingesting the same
    file is a no-op, so an export can be fed in repeatedly without ever
    duplicating or -- more importantly -- silently replacing the archived
    original that every downstream record points at.
    """
    src = Path(source).expanduser().resolve()
    digest = sha256_file(src)

    # `source` may already BE the archived copy -- re-analysing an existing
    # run feeds its own "Raw exports/<sha12>_<name>" path back in here.
    # Without this check, slugify(src.name) on a name that is already
    # content-addressed round-trips unchanged, so the naive target below
    # would double the prefix ("<sha12>_<sha12>_<name>") and silently create
    # a second, differently-named copy of the exact same bytes instead of
    # recognising the one that is already there.
    if src.parent == ws.raw.resolve() and src.name.startswith(f"{digest[:12]}_"):
        return src, digest

    target = ws.raw / f"{digest[:12]}_{slugify(src.name)}"

    if target.exists():
        # Content-addressed: an existing file with this name has this content.
        return target, digest

    ws.raw.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".partial")
    shutil.copy2(src, tmp)
    os.replace(tmp, target)
    try:
        os.chmod(target, 0o444)
    except OSError:
        # Read-only is a guard rail, not a requirement; some filesystems refuse.
        pass
    return target, digest


# ----------------------------------------------------------------------------
# JSON-safe conversion
# ----------------------------------------------------------------------------


def jsonable(value: Any) -> Any:
    """Convert numpy / pandas scalars to plain Python, and every flavour of
    missing value to None.

    JSON has no NaN, and a NaN that survives into a record would come back from
    a rebuild as the string 'NaN' and quietly poison later arithmetic. Missing
    is missing: it is written as null and read back as None.
    """
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(value, (np.ndarray,)):
        return [jsonable(v) for v in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (np.str_, str)):
        return str(value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def cycle_records(df: pd.DataFrame) -> list[dict]:
    """Per-cycle rows as plain dicts, missing values as None."""
    return [{str(k): jsonable(v) for k, v in row.items()} for row in df.to_dict("records")]


# ----------------------------------------------------------------------------
# The specimen record
# ----------------------------------------------------------------------------


def build_payload(
    test: TestData,
    df: pd.DataFrame,
    cfg: Config,
    *,
    material: str,
    raw_path: Optional[Path],
    source_sha256: str,
    workspace: Optional[Workspace] = None,
    gauge_length_confirmed: bool = False,
) -> dict:
    """Assemble everything needed to reproduce and index one specimen's result.

    Deliberately self-contained: metadata, the exact config used, the source
    file's identity and every per-cycle metric. A record plus the archived raw
    file is enough to re-run the analysis and get the same numbers.
    """
    attrs = df.attrs if not df.empty else {}
    rel_raw = workspace.relative(raw_path) if (workspace and raw_path) else (
        str(raw_path) if raw_path else None
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "specimen": {
            "specimen_id": specimen_id(source_sha256, test.label, material),
            "label": test.label,
            "material": material,
            # Operator-facing name -- what a human should read as "which file
            # was this". source_file below is the full ingest-time path,
            # which may be a temporary or session-specific location and is
            # not meaningful to a reader on a different machine.
            "source_filename": Path(test.source_file).name,
            "source_file": str(test.source_file),
            "source_format": test.source_format,
            "source_sha256": source_sha256,
            "raw_input_path": rel_raw,
            "displacement_channel": test.displacement_channel,
            "h0_mm": jsonable(test.h0_mm),
            "d0_mm": jsonable(test.d0_mm),
            "temperature_c": jsonable(test.temperature_c),
            "n_points": int(len(test.stress_mpa)),
            "notes": list(test.notes),
        },
        "analysis": {
            "n_cycles": int(len(df)),
            "global_peak_mpa": jsonable(attrs.get("global_peak_mpa")),
            "multi_stage": bool(attrs.get("multi_stage", False)),
            "residual_stress_mpa": jsonable(attrs.get("residual_stress_mpa")),
            # Auto-located common-band stiffness window (core.py) -- a
            # test-wide pair of bounds, found once from the reference cycle.
            "stiffness_common_lo_mpa": jsonable(attrs.get("stiffness_common_lo_mpa")),
            "stiffness_common_hi_mpa": jsonable(attrs.get("stiffness_common_hi_mpa")),
            "h0_mm": jsonable(attrs.get("h0_mm")),
            "has_strain": bool(attrs.get("h0_mm")),
            "notes": list(attrs.get("notes", [])),
            # What the strain columns were divided by, and whether anyone has
            # checked it. Travels with the record so a stored result can never
            # be read as validated strain when nobody confirmed the gauge length.
            "strain_basis": diagnostics.strain_basis(
                test, gauge_length_confirmed=gauge_length_confirmed
            ),
            # Conditions that change how these numbers should be read.
            "warnings": diagnostics.collect(
                test, df, cfg, gauge_length_confirmed=gauge_length_confirmed
            ),
        },
        "config": {k: jsonable(v) for k, v in asdict(cfg).items()},
        "cycles": cycle_records(df),
    }


def write_json(payload: dict, path: Path) -> Path:
    """Write the specimen record / run manifest -- the documented source of
    truth. Atomic (write a .partial, then os.replace): a plain `open(path,
    "w")` truncates the destination FIRST, so a reader on another process (or
    this one, after a dropped network-share connection) could observe a
    zero-byte or half-written file mid-write. archive_raw already uses this
    exact pattern for the raw copy; the record it points at needs it more,
    not less."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".partial")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def read_json(path: str | os.PathLike) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# How long a lock older than this is assumed abandoned by a crashed holder
# and stolen rather than left to wedge every future writer forever -- a
# network share has no process table to check a PID against, so age is the
# only signal available. How long a caller waits for a live holder before
# giving up and proceeding unlocked anyway: this buys correctness under the
# ordinary case of two people clicking around the same moment, not a hard
# guarantee under sustained contention -- consistent with this codebase's
# existing best-effort stance on shared, hand-editable files (see audit.py).
_LOCK_STALE_S = 30.0
_LOCK_TIMEOUT_S = 5.0
_LOCK_POLL_S = 0.05


@contextmanager
def locked_update(target: Path) -> Iterator[None]:
    """Serialises a read-modify-write cycle against `target` across
    processes (and threads) that might touch it at the same moment.

    materials.json and admins.json are small, shared, hand-editable lists:
    every "add a material" or "add an admin" call reads the whole file,
    computes old-list-plus-one-change, and writes the whole file back.
    write_json() alone makes each individual WRITE atomic, but not the
    read-modify-write as a whole -- two such calls racing each read the
    SAME stale list, each compute their own "plus one change" from it, and
    whichever writes second silently overwrites the first's change instead
    of both surviving. Wrapping the read, the modification and the write in
    one `with locked_update(...):` block is what actually closes that gap.

    A plain lock FILE, not a library: acquired by exclusive create
    (`os.O_CREAT | os.O_EXCL`), atomic on every filesystem this app
    targets, including SMB -- the same primitive `resolve_run_dir` already
    relies on for run-folder allocation, applied here to a single shared
    file instead of a new directory each time.
    """
    lock_path = target.with_name(target.name + ".lock")
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    acquired = False
    while time.monotonic() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                continue  # the previous holder released it just now; retry the create
            if age > _LOCK_STALE_S:
                lock_path.unlink(missing_ok=True)
                continue
            time.sleep(_LOCK_POLL_S)
    try:
        yield
    finally:
        if acquired:
            lock_path.unlink(missing_ok=True)


def payload_frame(payload: dict) -> pd.DataFrame:
    """Per-cycle table from a record, with column order restored from the spec."""
    from .schema import CYCLE_COLUMNS

    df = pd.DataFrame(payload.get("cycles", []))
    if df.empty:
        return df
    ordered = [c.key for c in CYCLE_COLUMNS if c.key in df.columns]
    extra = [c for c in df.columns if c not in ordered]
    return df[ordered + extra]


# ----------------------------------------------------------------------------
# Run directories
# ----------------------------------------------------------------------------


def run_dir_name(material: str, when: Optional[datetime] = None) -> str:
    when = when or datetime.now()
    return f"{slugify(material)}_{when.strftime('%Y-%m-%d')}"


def resolve_run_dir(
    ws: Workspace, material: str, fingerprint: str, when: Optional[datetime] = None
) -> Path:
    """Pick the output folder for a run, and claim it.

    Re-analysing the same sources with the same config on the same day is a
    re-run and overwrites in place, which keeps the tree from filling with
    identical folders. Anything else -- different files, different config --
    gets its own suffixed folder, so a changed result never silently displaces
    the earlier one it should be compared against.

    Claiming is exclusive-create (`mkdir` without `exist_ok`), not the
    exists()-then-mkdir this replaced: two ingests racing for the same
    candidate folder could both see it free, both proceed, and then both
    write specimens into it while write_manifest's run.json -- written once,
    at the end of each ingest -- silently keeps only the last writer's list,
    orphaning the other ingest's specimens from the manifest. Letting the
    filesystem itself arbitrate who created the directory removes the race
    instead of narrowing its window: the loser of a mkdir race simply moves
    on to try the next suffix, exactly as if it had found the folder already
    taken by an earlier run.
    """
    base = run_dir_name(material, when)
    candidate = ws.processed / base
    index = 1
    while True:
        try:
            candidate.mkdir(parents=True)
            return candidate
        except FileExistsError:
            pass
        manifest = candidate / "run.json"
        if manifest.exists():
            try:
                if read_json(manifest).get("fingerprint") == fingerprint:
                    return candidate
            except (json.JSONDecodeError, OSError):
                pass
        index += 1
        candidate = ws.processed / f"{base}-{index:03d}"


def run_fingerprint(source_hashes: Iterable[str], cfg: Config) -> str:
    joined = ",".join(sorted(source_hashes))
    return hashlib.sha256(f"{joined}|{config_hash(cfg)}".encode()).hexdigest()[:16]


def write_manifest(
    run_dir: Path,
    *,
    material: str,
    cfg: Config,
    fingerprint: str,
    sources: list[dict],
    specimens: list[dict],
) -> Path:
    """Index of one ingest run: what went in, under which settings, what came out."""
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "material": material,
        "fingerprint": fingerprint,
        "config": {k: jsonable(v) for k, v in asdict(cfg).items()},
        "sources": sources,
        "specimens": specimens,
    }
    return write_json(manifest, run_dir / "run.json")


def iter_specimen_jsons(ws: Workspace) -> list[Path]:
    """Every specimen record under Records/, run manifests excluded."""
    if not ws.processed.exists():
        return []
    return sorted(p for p in ws.processed.glob("*/*.json") if p.name != "run.json")
