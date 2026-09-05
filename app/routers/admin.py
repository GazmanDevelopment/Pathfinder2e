import re
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import authelia_sync
from app.config import ANTHROPIC_API_KEY, AUTHELIA_USERS_DB_PATH
from app.db import get_db
from app.models import Character, User
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])

_USERNAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
MIN_PASSWORD_LENGTH = 8


def _admin_count(db: Session) -> int:
    """All admin rows, active or disabled — a disabled admin can't be used
    to fix anything via the UI, so it still counts toward the guard rail."""
    return db.query(User).filter(User.role == "admin").count()


def _get_user_or_404(user_id: int, db: Session) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/users")
def list_users(
    request: Request,
    filter: str = "active",
    added: str | None = None,
    password: str | None = None,
    db: Session = Depends(get_db),
):
    if filter not in ("active", "disabled"):
        filter = "active"

    users = (
        db.query(User)
        .filter(User.is_disabled.is_(filter == "disabled"))
        .order_by(User.id)
        .all()
    )
    char_counts = dict(
        db.query(Character.user_id, func.count(Character.id)).group_by(Character.user_id).all()
    )

    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "users": users,
            "filter": filter,
            "added": added,
            "password_status": password,
            "char_counts": char_counts,
            "admin_count": _admin_count(db),
            "authelia_configured": bool(AUTHELIA_USERS_DB_PATH),
            "ai_levelup_configured": bool(ANTHROPIC_API_KEY),
        },
    )


@router.post("/users")
def add_user(email: str = Form(...), db: Session = Depends(get_db)):
    """Pre-register an email so its next login is accepted.

    Idempotent: an already-registered email is left untouched rather than
    duplicated or erroring — the admin sees it's already there either way.
    """
    email = email.strip().lower()
    existing = db.query(User).filter(User.email == email).one_or_none()
    if existing is None:
        db.add(User(email=email, role="member"))
        db.commit()
        status = "added"
    else:
        status = "already-registered"
    return RedirectResponse(url=f"/admin/users?added={status}", status_code=303)


@router.post("/users/{user_id}/disable")
def disable_user(user_id: int, db: Session = Depends(get_db)):
    """Block login immediately (require_login re-checks the DB every
    request) and drop the user's characters out of the default admin view,
    without touching their data — see get_character_or_404 for the reverse
    (admin can still open any character directly, including theirs)."""
    user = _get_user_or_404(user_id, db)
    if user.role == "admin" and _admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Can't disable the last remaining admin.")
    user.is_disabled = True
    db.commit()
    return RedirectResponse(url="/admin/users?filter=active", status_code=303)


@router.post("/users/{user_id}/enable")
def enable_user(user_id: int, db: Session = Depends(get_db)):
    user = _get_user_or_404(user_id, db)
    user.is_disabled = False
    db.commit()
    return RedirectResponse(url="/admin/users?filter=disabled", status_code=303)


@router.post("/users/{user_id}/grant-ai-levelup")
def grant_ai_levelup(user_id: int, filter: str = Form("active"), db: Session = Depends(get_db)):
    """Phase 8 — independent of role/is_disabled. Checked live against the
    DB on every /level-up request (see app/deps.py's
    require_ai_levelup_access), so revocation takes effect immediately even
    for an already-open session, same guarantee Phase 4b gave is_disabled."""
    user = _get_user_or_404(user_id, db)
    user.can_use_ai_levelup = True
    db.commit()
    return RedirectResponse(url=f"/admin/users?filter={filter}", status_code=303)


@router.post("/users/{user_id}/revoke-ai-levelup")
def revoke_ai_levelup(user_id: int, filter: str = Form("active"), db: Session = Depends(get_db)):
    user = _get_user_or_404(user_id, db)
    user.can_use_ai_levelup = False
    db.commit()
    return RedirectResponse(url=f"/admin/users?filter={filter}", status_code=303)


@router.post("/users/{user_id}/delete")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    """Hard removal. Only offered for a user who owns zero characters — this
    is never the action that destroys someone's character data; disable is,
    by design, the only path once a user has characters worth keeping."""
    user = _get_user_or_404(user_id, db)
    if user.role == "admin" and _admin_count(db) <= 1:
        raise HTTPException(status_code=400, detail="Can't delete the last remaining admin.")
    owns_characters = db.query(Character).filter(Character.user_id == user_id).count() > 0
    if owns_characters:
        raise HTTPException(
            status_code=400,
            detail="Can't delete a user who owns characters — disable them instead.",
        )
    filter_after = "disabled" if user.is_disabled else "active"
    db.delete(user)
    db.commit()
    return RedirectResponse(url=f"/admin/users?filter={filter_after}", status_code=303)


@router.post("/users/{user_id}/set-password")
def set_password(
    user_id: int,
    password: str = Form(...),
    username: str = Form(""),
    db: Session = Depends(get_db),
):
    """Sets/replaces a local (Authelia) account's password, computing the
    Argon2id hash and writing authelia/users_database.yml directly — see
    app/authelia_sync.py. Replaces the old SSH + hand-edit workflow.

    First call for a user: also picks and locks in `local_username` (no
    rename flow after). If that username already exists in the live YAML
    file — e.g. a hand-created entry from before this feature existed — and
    its email matches this user, "adopt" it (link the username, touch only
    the password); a mismatched email is rejected rather than silently
    overwriting an unrelated account.
    """
    if not AUTHELIA_USERS_DB_PATH:
        raise HTTPException(status_code=404, detail="Not found")

    user = _get_user_or_404(user_id, db)
    if user.auth_source == "entra":
        raise HTTPException(status_code=400, detail="Entra/SSO accounts don't use a local password.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )

    path = Path(AUTHELIA_USERS_DB_PATH)

    if user.local_username is None:
        candidate = username.strip().lower()
        if not _USERNAME_RE.match(candidate):
            raise HTTPException(
                status_code=400,
                detail="Username must be 1-32 characters: lowercase letters, digits, underscore, or hyphen.",
            )

        existing_entry = authelia_sync.find_entry(path, candidate)
        if existing_entry is not None:
            existing_email = (existing_entry.get("email") or "").strip().lower()
            if existing_email != user.email.strip().lower():
                raise HTTPException(
                    status_code=400,
                    detail="That username is already in use for a different account — choose another.",
                )
            # Same email: adopting a hand-created entry from before this
            # feature existed — upsert_local_account only touches the
            # password for an already-existing entry, so displayname/
            # groups/disabled are left as they already are.

        user.local_username = candidate
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=400, detail="That username is already taken.")
    else:
        candidate = user.local_username

    authelia_sync.upsert_local_account(
        path,
        username=candidate,
        display_name=user.display_name or user.email,
        email=user.email,
        password_hash=authelia_sync.hash_password(password),
    )

    return RedirectResponse(url="/admin/users?password=set", status_code=303)
