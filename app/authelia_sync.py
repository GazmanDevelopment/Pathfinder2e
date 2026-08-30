"""Reads and writes Authelia's users_database.yml directly (Phase 4c).

Pure stdlib + pyyaml + argon2-cffi, no FastAPI/DB dependency — this
manipulates a different system's credential file, a distinct concern from
the rest of the app's ORM-backed routes.
"""

import os
import tempfile
from pathlib import Path

import yaml
from argon2 import PasswordHasher, Type

# Matches Authelia's own default argon2 parameters exactly (memory 65536 KiB,
# 3 iterations, parallelism 4, 16-byte salt, 32-byte key) — see CLAUDE.md's
# Phase 4c spec. Verified directly: this produces the same
# "$argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>" PHC string Authelia's own
# CLI would, so the file backend accepts it with no config change.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


def hash_password(plaintext: str) -> str:
    return _hasher.hash(plaintext)


def _load(path: Path) -> dict:
    if not path.exists():
        return {"users": {}}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data:
        return {"users": {}}
    data.setdefault("users", {})
    return data


def find_entry(path: Path, username: str) -> dict | None:
    """The existing YAML entry for `username`, or None if there isn't one.

    Used before creating a new entry: `local_username` is a brand-new,
    unbackfilled DB column, so an account created by hand before Phase 4c
    existed (including an admin's own) may already occupy this username in
    the file without our DB knowing about it. Checking the DB alone isn't
    enough to avoid silently overwriting an unrelated entry.
    """
    return _load(path)["users"].get(username)


def _write(path: Path, data: dict) -> None:
    """Temp file in the same directory + os.replace(): atomic swap, so
    Authelia's file.watch never observes a half-written file mid-save. Only
    safe because the whole authelia/ directory is one bind mount — a temp
    file and its target must share a filesystem for os.replace() to be
    atomic rather than raising EXDEV.
    """
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".users_database.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def upsert_local_account(
    path: Path,
    *,
    username: str,
    display_name: str,
    email: str,
    password_hash: str,
    create_disabled: bool = False,
) -> None:
    """Create `username`'s entry if it doesn't exist yet, or replace just its
    password if it does. An existing entry's displayname/email/groups/
    disabled are left untouched — they may have been hand-edited, and this
    function is also used for the "adopt an existing hand-created entry"
    path, where only the password should change.
    """
    data = _load(path)
    users = data["users"]
    entry = users.get(username)
    if entry is None:
        users[username] = {
            "disabled": create_disabled,
            "displayname": display_name,
            "password": password_hash,
            "email": email,
            "groups": [],
        }
    else:
        entry["password"] = password_hash
    _write(path, data)
