import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.ai_levelup import AiLevelUpError, LevelUpProposal, archive_and_apply, send_turn
from app.db import get_db
from app.deps import require_ai_levelup_access
from app.models import Character, LevelUpSession
from app.templating import templates

router = APIRouter(prefix="/characters/{character_id}/level-up", tags=["level_up"])


def _get_session(character_id: int, db: Session) -> LevelUpSession | None:
    return db.query(LevelUpSession).filter(LevelUpSession.character_id == character_id).one_or_none()


def _transcript_context(
    request: Request, character: Character, session: LevelUpSession | None, error: str | None = None
) -> dict:
    """Builds the template context for the chat transcript. An assistant
    turn's stored content is the raw structured-output JSON (the whole
    serialized LevelUpTurn — that's what needs to be replayed back to
    Claude verbatim as conversation history), not plain prose — so for
    DISPLAY, each assistant turn is re-parsed here to pull out just its
    `message` text. Only the LATEST turn's proposal is ever shown as a
    diff; earlier turns' proposals (now superseded) aren't separately
    re-displayed in the transcript. `character` is passed through so the
    diff view can show each field's current value alongside the proposed
    one.
    """
    display_messages = []
    proposal = None
    if session is not None:
        for msg in json.loads(session.messages_json):
            if msg["role"] == "assistant":
                try:
                    text = json.loads(msg["content"]).get("message", msg["content"])
                except (json.JSONDecodeError, AttributeError):
                    text = msg["content"]
                display_messages.append({"role": "assistant", "content": text})
            else:
                display_messages.append(msg)
        if session.latest_proposal_json:
            proposal = LevelUpProposal.model_validate_json(session.latest_proposal_json)
    return {
        "request": request,
        "character_id": character.id,
        "character": character,
        "session": session,
        "messages": display_messages,
        "proposal": proposal,
        "error": error,
    }


@router.get("")
def show_level_up(
    request: Request, character_id: int, character: Character = Depends(require_ai_levelup_access),
    db: Session = Depends(get_db),
):
    """Always resumes an in-progress session if one exists — there's no
    competing 'start fresh' entry point while one's open, matching this
    app's "at most one in-progress session per character" design."""
    session = _get_session(character_id, db)
    return templates.TemplateResponse(
        "level_up/session.html", _transcript_context(request, character, session)
    )


@router.post("/start")
def start_level_up(
    request: Request, character_id: int, note: str = Form(...),
    character: Character = Depends(require_ai_levelup_access), db: Session = Depends(get_db),
):
    session = _get_session(character_id, db)
    if session is None:
        # Committed immediately, before the slow AI call below — not left
        # open across it. A write transaction (started by this insert)
        # holds SQLite's single writer lock for as long as it stays
        # uncommitted, and an AI call can run 30-120+ seconds; leaving it
        # open that whole time would block every other write anywhere in
        # the app for that entire window, not just this row (reproduced
        # directly: two overlapping writes with no intervening commit
        # raised "database is locked"). send_turn() below only mutates
        # this already-persisted object's attributes in memory; the
        # second commit further down persists those.
        session = LevelUpSession(character_id=character_id)
        db.add(session)
        db.commit()
        db.refresh(session)
    try:
        send_turn(character, session, note)
    except AiLevelUpError as exc:
        return templates.TemplateResponse(
            "level_up/_transcript.html", _transcript_context(request, character, session, error=str(exc))
        )
    db.commit()
    return templates.TemplateResponse(
        "level_up/_transcript.html", _transcript_context(request, character, session)
    )


@router.post("/message")
def post_message(
    request: Request, character_id: int, message: str = Form(...),
    character: Character = Depends(require_ai_levelup_access), db: Session = Depends(get_db),
):
    session = _get_session(character_id, db)
    if session is None:
        raise HTTPException(status_code=404, detail="No level-up session in progress.")
    try:
        send_turn(character, session, message)
    except AiLevelUpError as exc:
        return templates.TemplateResponse(
            "level_up/_transcript.html", _transcript_context(request, character, session, error=str(exc))
        )
    db.commit()
    return templates.TemplateResponse(
        "level_up/_transcript.html", _transcript_context(request, character, session)
    )


@router.post("/apply")
def apply_level_up(
    character_id: int,
    character: Character = Depends(require_ai_levelup_access),
    db: Session = Depends(get_db),
    accept_field: list[str] = Form([]),
    accept_spell: list[int] = Form([]),
    accept_equipment: list[int] = Form([]),
    accept_feature: list[int] = Form([]),
):
    session = _get_session(character_id, db)
    if session is None or session.latest_proposal_json is None:
        raise HTTPException(status_code=404, detail="Nothing to apply.")
    proposal = LevelUpProposal.model_validate_json(session.latest_proposal_json)
    archive_and_apply(
        character, proposal, accept_field, accept_spell, accept_equipment, accept_feature, session, db
    )
    db.commit()
    return RedirectResponse(url=f"/characters/{character_id}", status_code=303)


@router.post("/discard")
def discard_level_up(
    character_id: int, character: Character = Depends(require_ai_levelup_access), db: Session = Depends(get_db)
):
    """Deletes the session — no archive is ever created here, since nothing
    was applied."""
    session = _get_session(character_id, db)
    if session is not None:
        db.delete(session)
        db.commit()
    return RedirectResponse(url=f"/characters/{character_id}", status_code=303)
