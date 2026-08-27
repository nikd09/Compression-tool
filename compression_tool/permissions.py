"""
permissions.py
===============
Who may rename or delete a material: <workspace>/admins.json, a plain list
of OS usernames, matched against `getpass.getuser()` case-insensitively.

The same pattern as materials.json (material_registry.py) -- a small,
shared, hand-editable file sitting at the workspace root, not a login
system. This app has no authentication (see the deployment plan): nothing
here proves who is actually sitting at the keyboard, only what Windows
happens to report as the current account. It exists to keep the many
people who only read from ever seeing a Rename/Delete button, and to keep
an accidental click from a casual user from renaming or deleting a shared
material -- not to stop someone deliberately editing admins.json or
running as another Windows account. Real per-person enforcement needs the
app hosted behind corporate SSO, at which point this file becomes
unnecessary.

Before admins.json exists, every action this module gates is allowed for
everyone -- unchanged from today's behaviour, since there is nothing to
restrict access to yet. The first person to open the admin panel and claim
it becomes its only entry; every subsequent visitor is then restricted to
whoever is listed.
"""

from __future__ import annotations

import getpass
import logging

from .persistence import Workspace, locked_update, read_json, write_json

_FILENAME = "admins.json"
_log = logging.getLogger(__name__)


def current_user() -> str:
    """The OS account the app is running as -- the same identity
    audit.py already attributes every ingest to."""
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no resolvable login name is not
        # a reason to crash a permission check.
        return "unknown"


def load_admins(ws: Workspace) -> list[str]:
    try:
        data = read_json(ws.root / _FILENAME)
        names = data.get("admins", [])
        if isinstance(names, list) and all(isinstance(n, str) for n in names):
            return names
    except (OSError, ValueError):
        pass
    return []


def admins_configured(ws: Workspace) -> bool:
    return bool(load_admins(ws))


def is_admin(ws: Workspace) -> bool:
    """True if the current OS user may rename or delete a material.

    Unrestricted (True for everyone) until admins.json exists -- see the
    module docstring. Comparison is case-insensitive: Windows account names
    are not case-sensitive in practice, and a mismatch here would silently
    hide the admin controls from someone who is, in every sense that
    matters, already on the list.
    """
    admins = load_admins(ws)
    if not admins:
        return True
    me = current_user().casefold()
    return me in {a.casefold() for a in admins}


def claim_admin(ws: Workspace) -> list[str]:
    """Create admins.json with just the current user -- the one-time
    bootstrap action that turns access on. Refuses if the file already
    exists, so a second person clicking the same button cannot silently
    reset the list back down to just themselves.

    The exists-check and the write are inside one locked_update block, not
    two separate steps: without that, two people clicking "Claim admin
    access" at the same moment could both pass the check before either has
    written, and the second write would silently overwrite the first
    person's admins.json with a list containing only the second person --
    exactly the "reset back down to just themselves" this refusal exists to
    prevent, just via a race instead of a repeated click.
    """
    with locked_update(ws.root / _FILENAME):
        if admins_configured(ws):
            raise ValueError("admins.json already exists; add yourself from the admin list instead")
        names = [current_user()]
        write_json({"admins": names}, ws.root / _FILENAME)
        _log.info("claim_admin: %r claimed admin access in %s", names[0], ws.root)
        return names


def add_admin(ws: Workspace, name: str) -> list[str]:
    name = name.strip()
    if not name:
        raise ValueError("username cannot be empty")
    # See material_registry.add_material's matching comment: the read and
    # the write both have to be inside the lock, or two additions racing on
    # separate reads can silently lose one of them.
    with locked_update(ws.root / _FILENAME):
        names = load_admins(ws)
        if name.casefold() not in {n.casefold() for n in names}:
            names = names + [name]
            write_json({"admins": names}, ws.root / _FILENAME)
            _log.info("add_admin: %r added %r as admin in %s", current_user(), name, ws.root)
        return names


def remove_admin(ws: Workspace, name: str) -> list[str]:
    with locked_update(ws.root / _FILENAME):
        names = [n for n in load_admins(ws) if n.casefold() != name.casefold()]
        write_json({"admins": names}, ws.root / _FILENAME)
        _log.info("remove_admin: %r removed %r as admin in %s", current_user(), name, ws.root)
        return names
