"""
config_view.py's "Re-analyse this run" -- reusing a run's already-archived
source file(s) to re-run analysis with different thresholds, without asking
anyone to find and re-upload the original export.

_reanalyze_sources() is the pure, disk-only half (which of a run's sources
still have an archived copy to reuse) and is tested directly, without going
through Streamlit. The full round trip -- resolving those sources and
feeding them back through ingest() -- is tested against pipeline.ingest()
and persistence.read_json() directly, exercising exactly what the "Re-analyse
now" button's callback does.
"""

from __future__ import annotations

from compression_tool import Workspace, ingest
from compression_tool.core import Config
from compression_tool.persistence import read_json
from compression_tool.webapp.config_view import _reanalyze_sources


def test_reanalyze_sources_finds_an_archived_source(workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    result = ingest([single_file], ws, material="TALCO50")
    manifest = read_json(result.run_dir / "run.json")

    found, missing = _reanalyze_sources(ws, manifest)

    assert not missing
    assert len(found) == 1
    assert found[0].exists()
    assert found[0].parent == ws.raw


def test_reanalyze_sources_reports_a_never_archived_source_as_missing(workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    result = ingest([single_file], ws, material="TALCO50", archive_originals=False)
    manifest = read_json(result.run_dir / "run.json")

    found, missing = _reanalyze_sources(ws, manifest)

    assert not found
    assert missing == [manifest["sources"][0]["source_file"]]


def test_reanalyze_sources_reports_a_deleted_archive_as_missing(workspace, single_file):
    ws = Workspace.at(workspace).ensure()
    result = ingest([single_file], ws, material="TALCO50")
    manifest = read_json(result.run_dir / "run.json")
    archived = ws.root / manifest["sources"][0]["raw_input_path"]
    archived.chmod(0o644)
    archived.unlink()

    found, missing = _reanalyze_sources(ws, manifest)

    assert not found
    assert missing == [manifest["sources"][0]["source_file"]]


def test_reanalyzing_with_a_different_config_creates_a_new_run_and_reuses_the_archive(
    workspace, single_file
):
    """The behaviour _render_reanalyze's callback depends on: feeding the
    resolved archive path(s) back through ingest() with a changed Config
    must not touch the original run (a different fingerprint gets its own
    folder, resolve_run_dir's existing rule), and must not create a second
    copy of the already-archived source (archive_raw's idempotency, see
    test_persistence.py's matching pin)."""
    ws = Workspace.at(workspace).ensure()
    first = ingest([single_file], ws, material="TALCO50")
    manifest = read_json(first.run_dir / "run.json")
    found, missing = _reanalyze_sources(ws, manifest)
    assert not missing

    changed_cfg = Config(unload_frac=0.05)
    second = ingest(
        found, ws, material=manifest["material"], cfg=changed_cfg,
        archive_originals=True, write_reports=True,
    )

    assert second.run_dir != first.run_dir
    assert first.run_dir.exists()
    assert len(list(ws.raw.iterdir())) == 1

    second_manifest = read_json(second.run_dir / "run.json")
    assert (
        second_manifest["sources"][0]["raw_input_path"]
        == manifest["sources"][0]["raw_input_path"]
    )


def test_reanalyzing_with_the_same_config_overwrites_the_run_in_place(workspace, single_file):
    """Same sources, same config, same day: resolve_run_dir's existing
    "re-run, not a new folder" rule -- re-analysing without touching a
    threshold is idempotent, not a growing pile of near-identical runs."""
    ws = Workspace.at(workspace).ensure()
    first = ingest([single_file], ws, material="TALCO50")
    manifest = read_json(first.run_dir / "run.json")
    found, _ = _reanalyze_sources(ws, manifest)

    second = ingest(
        found, ws, material=manifest["material"], cfg=Config(),
        archive_originals=True, write_reports=True,
    )

    assert second.run_dir == first.run_dir
