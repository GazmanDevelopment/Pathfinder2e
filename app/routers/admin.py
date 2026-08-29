from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Character, User
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])


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
            "char_counts": char_counts,
            "admin_count": _admin_count(db),
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
