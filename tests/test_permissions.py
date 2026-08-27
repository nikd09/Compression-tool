"""
permissions.py: the admins.json allowlist gating Rename/Delete on the
Materials tab.

Not a login system (see the module docstring) -- these tests pin the one
property that actually matters: unrestricted until someone deliberately
opts in by claiming admin access, restricted to exactly that list
afterwards, and the bootstrap claim can only ever happen once.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from compression_tool import Workspace
from compression_tool import permissions as perm


def test_unrestricted_before_admins_json_exists(workspace):
    ws = Workspace.at(workspace).ensure()
    assert perm.admins_configured(ws) is False
    assert perm.is_admin(ws) is True


def test_claim_admin_creates_the_file_with_the_current_user(workspace):
    ws = Workspace.at(workspace).ensure()
    names = perm.claim_admin(ws)
    assert names == [perm.current_user()]
    assert perm.admins_configured(ws) is True
    assert perm.is_admin(ws) is True


def test_claim_admin_refuses_once_already_configured(workspace):
    ws = Workspace.at(workspace).ensure()
    perm.claim_admin(ws)
    with pytest.raises(ValueError):
        perm.claim_admin(ws)


def test_is_admin_false_once_restricted_to_someone_else(workspace):
    ws = Workspace.at(workspace).ensure()
    perm.add_admin(ws, "someone-else-entirely")
    assert perm.is_admin(ws) is False


def test_admin_check_is_case_insensitive(workspace):
    ws = Workspace.at(workspace).ensure()
    perm.add_admin(ws, perm.current_user().upper())
    assert perm.is_admin(ws) is True


def test_add_admin_does_not_duplicate(workspace):
    ws = Workspace.at(workspace).ensure()
    perm.add_admin(ws, "alice")
    perm.add_admin(ws, "ALICE")
    assert perm.load_admins(ws) == ["alice"]


def test_remove_admin(workspace):
    ws = Workspace.at(workspace).ensure()
    perm.add_admin(ws, "alice")
    perm.add_admin(ws, "bob")
    perm.remove_admin(ws, "alice")
    assert perm.load_admins(ws) == ["bob"]


def test_concurrent_additions_of_different_admins_all_survive(workspace):
    """Same race as material_registry's matching test, against admins.json
    instead: without persistence.locked_update serialising add_admin's own
    read-modify-write, N threads adding different names at once can each
    read the file before any of the others have written theirs back, and
    every write but the last silently loses its addition."""
    ws = Workspace.at(workspace).ensure()
    perm.claim_admin(ws)  # bootstraps admins.json so every add_admin below is a plain append
    names = [f"user-{i:02d}" for i in range(12)]

    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        list(pool.map(lambda n: perm.add_admin(ws, n), names))

    stored = {n.casefold() for n in perm.load_admins(ws)}
    assert stored == {perm.current_user().casefold()} | {n.casefold() for n in names}


def test_only_one_concurrent_claim_admin_call_wins(workspace):
    """claim_admin's own refuse-if-already-configured check plus its write
    are inside one lock: two people racing to claim admin access at the
    same moment must end with exactly one person's name on the list, never
    a silent overwrite of the winner by the loser (which would look
    identical to the "reset back down to just themselves" the ValueError
    already guards against on a repeated, non-concurrent click)."""
    ws = Workspace.at(workspace).ensure()

    def try_claim(_):
        try:
            return perm.claim_admin(ws)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(try_claim, range(8)))

    successes = [r for r in results if r is not None]
    assert len(successes) == 1
    assert perm.load_admins(ws) == successes[0]
