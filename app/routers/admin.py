from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users")
def list_users(request: Request, added: str | None = None, db: Session = Depends(get_db)):
    users = db.query(User).order_by(User.id).all()
    return templates.TemplateResponse(
        "admin/users.html", {"request": request, "users": users, "added": added}
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
