"""
permissions.py: the admins.json allowlist gating Rename/Delete on the
Materials tab.

Not a login system (see the module docstring) -- these tests pin the one
property that actually matters: unrestricted until someone deliberately
opts in by claiming admin access, restricted to exactly that list
afterwards, and the bootstrap claim can only ever happen once.
"""

from __future__ import annotations

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
