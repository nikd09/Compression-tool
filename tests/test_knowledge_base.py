"""
The SQLite index.

Its defining property is that it is disposable: everything in it comes from the
JSON records, and deleting the file must cost nothing but the time to rebuild.
Most of these tests exist to keep that true.
"""

from __future__ import annotations

import pytest

from compression_tool import Workspace, ingest, rebuild_index
from compression_tool import knowledge_base as kb
from compression_tool.schema import CYCLE_COLUMNS, SPECIMEN_FIELDS


def test_ingest_populates_the_index(workspace, series_file):
    result = ingest([series_file], workspace, material="PEEK")
    assert result.indexed == 2

    conn = kb.connect(result.workspace.db_path)
    try:
        specimens = kb.list_specimens(conn)
        assert len(specimens) == 2
        assert set(specimens["material"]) == {"PEEK"}
        assert set(specimens["n_cycles"]) == {9}

        cycles = kb.cycles_for(conn, result.specimens[0].specimen_id)
        assert len(cycles) == 9
        assert list(cycles["Cycle"]) == list(range(1, 10))
    finally:
        conn.close()


def test_rebuild_reproduces_the_index_exactly(workspace, series_file, single_file):
    """The database is an index, not the source of truth. Deleting it and
    rebuilding must land in the same place."""
    ingest([series_file], workspace, material="PEEK")
    ingest([single_file], workspace, material="TALCO50")
    ws = Workspace.at(workspace)

    conn = kb.connect(ws.db_path)
    before_spec = kb.list_specimens(conn).sort_values("specimen_id").reset_index(drop=True)
    before_cycles = kb.query(conn, 'SELECT * FROM cycles ORDER BY "specimen_id", "Cycle"')
    conn.close()

    ws.db_path.unlink()
    assert rebuild_index(ws) == 3

    conn = kb.connect(ws.db_path)
    after_spec = kb.list_specimens(conn).sort_values("specimen_id").reset_index(drop=True)
    after_cycles = kb.query(conn, 'SELECT * FROM cycles ORDER BY "specimen_id", "Cycle"')
    conn.close()

    from pandas.testing import assert_frame_equal

    assert_frame_equal(before_spec, after_spec)
    assert_frame_equal(before_cycles, after_cycles)


def test_rebuild_creates_a_not_yet_existing_index_directory(workspace, series_file, tmp_path):
    """A Workspace with index_root pointing somewhere that has never been
    created (a fresh machine, or a cleared local cache -- the normal state
    the very first time the webapp opens a shared workspace) must not fail
    just because the directory does not exist yet."""
    ingest([series_file], workspace, material="PEEK")

    index_root = tmp_path / "not_yet_created" / "index"
    assert not index_root.exists()
    ws = Workspace.at(workspace, index_root=index_root)

    assert rebuild_index(ws) == 2
    assert ws.db_path.exists()
    assert ws.db_path.parent == index_root


def test_rebuild_survives_a_corrupt_record(workspace, series_file, single_file):
    """One unreadable file must not stop the rest of the archive indexing."""
    ingest([series_file], workspace, material="PEEK")
    result = ingest([single_file], workspace, material="TALCO50")
    ws = Workspace.at(workspace)

    result.specimens[0].json_path.write_text("{ this is not json", encoding="utf-8")
    assert rebuild_index(ws) == 2


def test_reingesting_replaces_rather_than_duplicates(workspace, series_file):
    ingest([series_file], workspace, material="PEEK")
    ingest([series_file], workspace, material="PEEK")

    conn = kb.connect(Workspace.at(workspace).db_path)
    try:
        assert len(kb.list_specimens(conn)) == 2
        assert kb.query(conn, "SELECT COUNT(*) AS n FROM cycles")["n"].iloc[0] == 18
    finally:
        conn.close()


def test_cycles_cascade_when_a_specimen_is_removed(workspace, series_file):
    result = ingest([series_file], workspace, material="PEEK")
    sid = result.specimens[0].specimen_id

    conn = kb.connect(result.workspace.db_path)
    try:
        conn.execute('DELETE FROM specimens WHERE "specimen_id" = ?', (sid,))
        conn.commit()
        assert kb.cycles_for(conn, sid).empty
    finally:
        conn.close()


def test_every_schema_column_exists_in_the_database(workspace, series_file):
    """schema.py is the single source of truth; the tables must follow it."""
    result = ingest([series_file], workspace, material="PEEK")
    conn = kb.connect(result.workspace.db_path)
    try:
        spec_cols = {r["name"] for r in conn.execute("PRAGMA table_info(specimens)")}
        cycle_cols = {r["name"] for r in conn.execute("PRAGMA table_info(cycles)")}
    finally:
        conn.close()

    assert {f.key for f in SPECIMEN_FIELDS} <= spec_cols
    assert {c.key for c in CYCLE_COLUMNS} <= cycle_cols
    assert "Stiffness_common_quality" in cycle_cols


def test_quality_flag_is_stored_for_querying(workspace, series_file):
    result = ingest([series_file], workspace, material="PEEK")
    conn = kb.connect(result.workspace.db_path)
    try:
        flags = kb.query(conn, 'SELECT DISTINCT "Stiffness_common_quality" AS q FROM cycles')
        assert set(flags["q"]) <= {"ok", "few points", "nonlinear", "none"}
        assert "ok" in set(flags["q"])
    finally:
        conn.close()


def test_materials_and_cross_material_query(workspace, series_file, single_file):
    ingest([series_file], workspace, material="PEEK")
    ingest([single_file], workspace, material="TALCO50")

    conn = kb.connect(Workspace.at(workspace).db_path)
    try:
        assert kb.materials(conn) == ["PEEK", "TALCO50"]

        joined = kb.cycles_for_materials(conn, ["PEEK", "TALCO50"])
        assert len(joined) == 27
        assert set(joined["material"]) == {"PEEK", "TALCO50"}
        assert kb.cycles_for_materials(conn, []).empty
    finally:
        conn.close()


def test_cycles_for_specimens_returns_only_those_specimens(workspace, series_file, single_file):
    """A comparison group can freely mix specimens across materials -- this is
    the query it is built from, so it must not silently pull in siblings."""
    ingest([series_file], workspace, material="PEEK")
    ingest([single_file], workspace, material="TALCO50")

    conn = kb.connect(Workspace.at(workspace).db_path)
    try:
        specimens = kb.list_specimens(conn)
        peek_s1 = specimens.loc[specimens["label"].str.endswith("1"), "specimen_id"].iloc[0]
        talco = specimens.loc[specimens["material"] == "TALCO50", "specimen_id"].iloc[0]

        df = kb.cycles_for_specimens(conn, [peek_s1, talco])
        assert set(df["specimen_id"]) == {peek_s1, talco}
        assert set(df["material"]) == {"PEEK", "TALCO50"}
        # 9 cycles each, from two different materials -- a group spanning
        # materials, exactly what a custom comparison group needs to do.
        assert len(df) == 18

        assert kb.cycles_for_specimens(conn, []).empty
    finally:
        conn.close()


def test_cross_material_query_has_no_duplicate_columns(workspace, series_file):
    """The join has specimen_id on both sides. Selecting it from both returns
    two columns of that name, and any consumer that then asks for it by label
    raises instead of getting a column -- which is how the Compare view's row
    table broke."""
    ingest([series_file], workspace, material="PEEK")
    conn = kb.connect(Workspace.at(workspace).db_path)
    try:
        joined = kb.cycles_for_materials(conn, ["PEEK"])
        assert list(joined.columns) == list(dict.fromkeys(joined.columns))
        # And it is still there exactly once -- not dropped entirely.
        assert joined["specimen_id"].ndim == 1
        assert joined[["material", "label", "specimen_id", "Cycle"]].shape[1] == 4
    finally:
        conn.close()


def test_index_can_be_skipped(workspace, series_file):
    result = ingest([series_file], workspace, material="PEEK", update_index=False)
    assert result.indexed == 0
    assert not result.workspace.db_path.exists()

    assert rebuild_index(result.workspace) == 2


def test_notes_survive_into_the_index(workspace, single_file):
    """The single-format export carries two displacement channels; the note
    saying which was used must reach the database."""
    result = ingest([single_file], workspace, material="TALCO50")
    conn = kb.connect(result.workspace.db_path)
    try:
        row = kb.list_specimens(conn).iloc[0]
        assert row["displacement_channel"] == "Sonder LAA"
    finally:
        conn.close()


def test_connect_rejects_a_mismatched_schema_version(workspace, series_file):
    """CREATE TABLE IF NOT EXISTS (ensure_schema) never migrates an
    existing table's columns -- an index built under a different
    SCHEMA_VERSION has to be caught explicitly, or it fails later as a
    raw, confusing sqlite3.OperationalError the first time some INSERT
    names a column the old table does not have."""
    result = ingest([series_file], workspace, material="PEEK")
    conn = kb.connect(result.workspace.db_path)
    conn.execute(
        'UPDATE meta SET "value" = ? WHERE "key" = ?', ("999", "schema_version")
    )
    conn.commit()
    conn.close()

    with pytest.raises(kb.SchemaVersionMismatch, match="999"):
        kb.connect(result.workspace.db_path)


def test_rebuild_recovers_from_a_mismatched_schema_version(workspace, series_file):
    """rebuild() is the documented fix for exactly this -- it drops and
    recreates the schema itself rather than calling connect(), so it must
    never be blocked by the same check connect() enforces."""
    result = ingest([series_file], workspace, material="PEEK")
    conn = kb.connect(result.workspace.db_path)
    conn.execute(
        'UPDATE meta SET "value" = ? WHERE "key" = ?', ("999", "schema_version")
    )
    conn.commit()
    conn.close()

    assert rebuild_index(result.workspace) == 2
    conn = kb.connect(result.workspace.db_path)  # no longer raises
    conn.close()
