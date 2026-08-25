"""
audit.py: who ingested what, and when.

Nothing downstream depends on this succeeding -- these tests exist to pin
that a normal ingest leaves a readable trail, that every entry point writes
one automatically, and that a failure to write the trail can never surface
as a failure of the ingest itself.
"""

from __future__ import annotations

from compression_tool import Workspace, ingest, list_audit_entries
from compression_tool import audit as audit_mod


def test_ingest_writes_one_audit_record(workspace, series_file):
    result = ingest([series_file], workspace, material="PEEK")
    assert result.audit_path is not None
    assert result.audit_path.exists()
    assert result.audit_path.parent.name == "audit"

    entries = list_audit_entries(result.workspace)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["material"] == "PEEK"
    assert entry["user"]
    assert entry["host"]
    assert entry["timestamp_utc"]
    assert len(entry["specimens"]) == 2
    assert len(entry["sources"]) == 1
    assert entry["sources"][0]["sha256"]
    assert entry["skipped"] == []


def test_audit_records_a_run_with_everything_skipped(workspace, tmp_path):
    """A batch where every file failed is still worth knowing about --
    someone tried to ingest something and it did not work."""
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"not an excel file at all")

    result = ingest([broken], workspace, material="junk")
    ws = result.workspace

    entries = list_audit_entries(ws)
    assert len(entries) == 1
    assert entries[0]["specimens"] == []
    assert len(entries[0]["skipped"]) == 1
    assert entries[0]["skipped"][0]["name"] == "broken.xlsx"


def test_audit_records_even_when_indexing_is_skipped(workspace, single_file):
    result = ingest([single_file], workspace, material="TALCO50", update_index=False)
    assert result.audit_path is not None
    assert len(list_audit_entries(result.workspace)) == 1


def test_list_entries_is_newest_first_and_respects_limit(workspace, single_file):
    ingest([single_file], workspace, material="A")
    ingest([single_file], workspace, material="B")
    ingest([single_file], workspace, material="C")
    ws = Workspace.at(workspace)

    entries = list_audit_entries(ws)
    assert [e["material"] for e in entries] == ["C", "B", "A"]

    assert len(list_audit_entries(ws, limit=2)) == 2


def test_no_audit_directory_is_an_empty_list_not_an_error(workspace):
    ws = Workspace.at(workspace).ensure()
    assert list_audit_entries(ws) == []


def test_record_ingest_never_raises_when_the_write_fails(workspace, monkeypatch):
    """A read-only share, a permissions problem, a full disk -- none of it
    may propagate up into an ingest that already wrote every specimen."""
    ws = Workspace.at(workspace).ensure()

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(audit_mod, "write_json", _boom)
    result = audit_mod.record_ingest(
        ws, material="PEEK", run_dir=ws.processed / "PEEK_2026-01-01",
        sources=[], specimens=[], skipped=[],
    )
    assert result is None
