from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Character


def get_character_or_404(character_id: int, request: Request, db: Session = Depends(get_db)) -> Character:
    """Load a character, enforcing ownership (or admin) alongside existence.

    Used both as an injected dependency (routes that need the Character
    object) and as a bare guard on the six child-resource routers included
    in main.py, which don't otherwise re-check ownership on their per-item
    routes. A missing character and someone else's character return the
    identical 404 — the response never reveals that a character exists but
    belongs to someone else.
    """
    character = db.get(Character, character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="Character not found")

    user = request.session.get("user")
    if user is None or (user["role"] != "admin" and character.user_id != user["id"]):
        raise HTTPException(status_code=404, detail="Character not found")

    return character
