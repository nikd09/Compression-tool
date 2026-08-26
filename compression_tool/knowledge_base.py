"""
knowledge_base.py
=================
SQLite index over the JSON records.

The database is never the source of truth. It holds nothing that is not
already in Records/, and `rebuild()` throws it away and regenerates
it from disk. That is the property worth protecting: if the schema changes, or
the file is corrupted, or a record is edited by hand, the fix is always to
rebuild rather than to migrate.

Two tables:
    specimens   one row per analysed specimen
    cycles      one row per cycle, cascading off its specimen
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from .persistence import Workspace, iter_specimen_jsons, read_json
from .schema import (
    CYCLE_COLUMNS,
    HOLD_DISP_RATE,
    SCHEMA_VERSION,
    SPECIMEN_FIELDS,
    STIFFNESS_QUALITY,
    UNLOAD_YIELD,
    hold_disp_per_1000_samples,
    stiffness_quality,
    unload_yield_frac,
)

# Stored alongside the derived columns so a query can filter on fit quality
# without re-deriving it. Rebuild recomputes it, so it cannot drift.
_CYCLE_STORED = tuple(CYCLE_COLUMNS) + (STIFFNESS_QUALITY, HOLD_DISP_RATE, UNLOAD_YIELD)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def specimens_ddl() -> str:
    cols = [f"  {_quote(f.key)} {f.sql}" for f in SPECIMEN_FIELDS]
    cols[0] += " PRIMARY KEY"
    return "CREATE TABLE IF NOT EXISTS specimens (\n" + ",\n".join(cols) + "\n)"


def cycles_ddl() -> str:
    cols = ['  "specimen_id" TEXT NOT NULL']
    cols += [f"  {_quote(c.key)} {c.sql}" for c in _CYCLE_STORED]
    cols.append('  PRIMARY KEY ("specimen_id", "Cycle")')
    cols.append('  FOREIGN KEY ("specimen_id") REFERENCES specimens("specimen_id") '
                "ON DELETE CASCADE")
    return "CREATE TABLE IF NOT EXISTS cycles (\n" + ",\n".join(cols) + "\n)"


META_DDL = 'CREATE TABLE IF NOT EXISTS meta ("key" TEXT PRIMARY KEY, "value" TEXT)'

INDEX_DDL = (
    'CREATE INDEX IF NOT EXISTS idx_specimens_material ON specimens("material")',
    'CREATE INDEX IF NOT EXISTS idx_specimens_label ON specimens("label")',
    'CREATE INDEX IF NOT EXISTS idx_cycles_specimen ON cycles("specimen_id")',
)


# ----------------------------------------------------------------------------
# Connection
# ----------------------------------------------------------------------------


class SchemaVersionMismatch(RuntimeError):
    """The index on disk was built under a different SCHEMA_VERSION than
    this code expects. `ensure_schema()`'s CREATE TABLE IF NOT EXISTS never
    migrates an existing table's columns -- left alone, a real mismatch
    (a column added or renamed since) would surface later as a raw
    sqlite3.OperationalError from whichever INSERT first names a column
    the old table does not have, nowhere near here and with no hint that a
    rebuild is what it actually needs. rebuild() (drop then recreate) is
    the fix -- it is unaffected by this check, it opens its own connection
    and does not call connect()."""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _check_schema_version(conn)
    ensure_schema(conn)
    return conn


def _check_schema_version(conn: sqlite3.Connection) -> None:
    try:
        row = conn.execute(
            'SELECT "value" FROM meta WHERE "key" = ?', ("schema_version",)
        ).fetchone()
    except sqlite3.OperationalError:
        return  # no meta table yet -- a brand-new database, nothing to check
    if row is None or row[0] == str(SCHEMA_VERSION):
        return
    raise SchemaVersionMismatch(
        f"This index was built under schema {row[0]!r}; this version of the "
        f"tool expects schema {SCHEMA_VERSION!r}. Rebuild the index from "
        f"disk (Config -> \"Reindex from disk\", or `compression-tool "
        f"rebuild` on the CLI) rather than using it as it is -- the JSON "
        f"records under Records/ are unaffected either way."
    )


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(specimens_ddl())
    conn.execute(cycles_ddl())
    conn.execute(META_DDL)
    for stmt in INDEX_DDL:
        conn.execute(stmt)
    conn.execute(
        'INSERT INTO meta ("key", "value") VALUES (?, ?) '
        'ON CONFLICT("key") DO UPDATE SET "value" = excluded."value"',
        ("schema_version", str(SCHEMA_VERSION)),
    )
    conn.commit()


def drop_schema(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS cycles")
    conn.execute("DROP TABLE IF EXISTS specimens")
    conn.execute("DROP TABLE IF EXISTS meta")
    conn.commit()


# ----------------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------------


def _as_sql(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (list, tuple)):
        return "; ".join(str(v) for v in value)
    return value


def upsert_payload(
    conn: sqlite3.Connection,
    payload: dict,
    *,
    json_path: Optional[str] = None,
    run_dir: Optional[str] = None,
) -> str:
    """Index one specimen record. Replaces any earlier row for the same id."""
    spec = dict(payload.get("specimen", {}))
    spec.update(payload.get("analysis", {}))
    spec["created_utc"] = payload.get("created_utc")
    if json_path is not None:
        spec["json_path"] = json_path
    if run_dir is not None:
        spec["run_dir"] = run_dir

    sid = spec.get("specimen_id")
    if not sid:
        raise ValueError("payload has no specimen_id")

    keys = [f.key for f in SPECIMEN_FIELDS]
    values = [_as_sql(spec.get(k)) for k in keys]
    placeholders = ", ".join("?" for _ in keys)
    conn.execute(
        f"INSERT OR REPLACE INTO specimens ({', '.join(_quote(k) for k in keys)}) "
        f"VALUES ({placeholders})",
        values,
    )

    conn.execute('DELETE FROM cycles WHERE "specimen_id" = ?', (sid,))
    cycle_keys = ["specimen_id"] + [c.key for c in _CYCLE_STORED]
    cycle_ph = ", ".join("?" for _ in cycle_keys)
    rows = []
    for cyc in payload.get("cycles", []):
        row = [sid]
        for col in _CYCLE_STORED:
            if col.key == STIFFNESS_QUALITY.key:
                row.append(stiffness_quality(cyc.get("Stiffness_common_n"),
                                             cyc.get("Stiffness_common_r2")))
            elif col.key == UNLOAD_YIELD.key:
                row.append(unload_yield_frac(cyc.get("StressAtMaxDisp_MPa"),
                                             cyc.get("PeakStress_MPa")))
            elif col.key == HOLD_DISP_RATE.key:
                row.append(hold_disp_per_1000_samples(cyc.get("Creep_during_hold_mm"),
                                                      cyc.get("HoldPoints")))
            else:
                row.append(_as_sql(cyc.get(col.key)))
        rows.append(row)
    if rows:
        conn.executemany(
            f"INSERT INTO cycles ({', '.join(_quote(k) for k in cycle_keys)}) "
            f"VALUES ({cycle_ph})",
            rows,
        )
    conn.commit()
    return sid


def index_payloads(conn: sqlite3.Connection, payloads: Iterable[dict]) -> int:
    count = 0
    for payload in payloads:
        upsert_payload(
            conn,
            payload,
            json_path=payload.get("_json_path"),
            run_dir=payload.get("_run_dir"),
        )
        count += 1
    return count


def rebuild(ws: Workspace, *, db_path: Optional[Path] = None) -> int:
    """Discard the index and regenerate it from every record on disk.

    Safe to run at any time: the records are authoritative, so the worst case
    of a rebuild is the time it takes.
    """
    target = Path(db_path) if db_path else ws.db_path
    # connect() does this mkdir itself; this function opens its own raw
    # connection instead (it needs drop_schema before ensure_schema, which
    # connect() doesn't offer), so it has to do the same thing here or
    # rebuilding a workspace whose index directory doesn't exist yet fails
    # outright -- confirmed live: exactly what happened the first time a
    # workspace used a not-yet-created index_root.
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    try:
        drop_schema(conn)
        ensure_schema(conn)
        count = 0
        for json_path in iter_specimen_jsons(ws):
            try:
                payload = read_json(json_path)
            except (OSError, ValueError):
                continue
            if "specimen" not in payload:
                continue
            upsert_payload(
                conn,
                payload,
                json_path=ws.relative(json_path),
                run_dir=ws.relative(json_path.parent),
            )
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------------


def query(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=tuple(params))


def list_specimens(conn: sqlite3.Connection, material: Optional[str] = None) -> pd.DataFrame:
    if material:
        return query(
            conn,
            'SELECT * FROM specimens WHERE "material" = ? ORDER BY "created_utc" DESC, "label"',
            (material,),
        )
    return query(conn, 'SELECT * FROM specimens ORDER BY "created_utc" DESC, "label"')


def materials(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        'SELECT DISTINCT "material" FROM specimens WHERE "material" IS NOT NULL '
        'ORDER BY "material"'
    ).fetchall()
    return [r[0] for r in rows]


def cycles_for(conn: sqlite3.Connection, specimen_id: str) -> pd.DataFrame:
    return query(
        conn, 'SELECT * FROM cycles WHERE "specimen_id" = ? ORDER BY "Cycle"', (specimen_id,)
    )


def cycles_for_specimens(conn: sqlite3.Connection, specimen_ids: Iterable[str]) -> pd.DataFrame:
    """Per-cycle rows joined to specimen identity, for an explicit set of
    specimens rather than a whole material -- what a custom comparison group
    is built from (any specimens, from any materials, in any combination)."""
    ids = list(specimen_ids)
    if not ids:
        return pd.DataFrame()
    marks = ", ".join("?" for _ in ids)
    return query(
        conn,
        f'SELECT s."material", s."label", s."h0_mm", '
        f'       s."global_peak_mpa", s."multi_stage", c.* '
        f"FROM cycles c JOIN specimens s ON s.\"specimen_id\" = c.\"specimen_id\" "
        f'WHERE c."specimen_id" IN ({marks}) '
        f'ORDER BY s."material", s."label", c."Cycle"',
        ids,
    )


def cycles_for_materials(conn: sqlite3.Connection, names: Iterable[str]) -> pd.DataFrame:
    """Per-cycle rows joined to specimen identity -- the shape the Compare view
    needs to overlay several materials."""
    names = list(names)
    if not names:
        return pd.DataFrame()
    marks = ", ".join("?" for _ in names)
    # c.* already carries specimen_id, so it is NOT selected from s as well:
    # two columns of the same name come back as a duplicate in the DataFrame,
    # and anything that then selects that label by name raises rather than
    # picking one.
    return query(
        conn,
        f'SELECT s."material", s."label", s."h0_mm", '
        f'       s."global_peak_mpa", s."multi_stage", c.* '
        f"FROM cycles c JOIN specimens s ON s.\"specimen_id\" = c.\"specimen_id\" "
        f'WHERE s."material" IN ({marks}) '
        f'ORDER BY s."material", s."label", c."Cycle"',
        names,
    )


__all__ = [
    "connect",
    "SchemaVersionMismatch",
    "ensure_schema",
    "rebuild",
    "upsert_payload",
    "index_payloads",
    "list_specimens",
    "materials",
    "cycles_for",
    "cycles_for_materials",
    "cycles_for_specimens",
    "query",
]
