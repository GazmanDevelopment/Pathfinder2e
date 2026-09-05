from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import ai_levelup_configured
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


def get_writable_character(character: Character = Depends(get_character_or_404)) -> Character:
    """Same ownership/existence check as get_character_or_404, but also
    rejects any request against an archived character (Phase 8) — archives
    are read-only history. Blocks on every HTTP method, not just mutating
    ones: simpler than a method-aware variant, and nothing in the read-only
    UI links to the harmless GETs this also blocks (e.g. an archived
    spell's own edit-form GET)."""
    if character.is_archived:
        raise HTTPException(status_code=403, detail="This character is archived and read-only.")
    return character


def require_ai_levelup_access(character: Character = Depends(get_writable_character)) -> Character:
    """Gates every /level-up route. 404, not 403, when the feature is
    simply unconfigured or not granted to this user — same 'hide, don't
    reveal' pattern as AUTHELIA_USERS_DB_PATH/set_password. Checked live
    against character.owner (not the session-cached role dict), so a
    revoked permission takes effect immediately even for an already-open
    browser tab, the same guarantee Phase 4b gave is_disabled."""
    if not ai_levelup_configured():
        raise HTTPException(status_code=404, detail="Not found")
    owner = character.owner
    if not (owner.can_use_ai_levelup or owner.role == "admin"):
        raise HTTPException(status_code=404, detail="Not found")
    return character
