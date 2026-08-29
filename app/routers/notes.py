from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_character_or_404
from app.models import Character, Note
from app.templating import render_fragment, templates

router = APIRouter(prefix="/characters/{character_id}/notes", tags=["notes"])


def _get_or_404(character_id: int, note_id: int, db: Session) -> Note:
    note = db.get(Note, note_id)
    if note is None or note.character_id != character_id:
        raise HTTPException(status_code=404, detail="Note not found")
    return note


@router.get("/new")
def new_note(request: Request, character_id: int, character: Character = Depends(get_character_or_404)):
    return templates.TemplateResponse(
        "notes/_row_edit.html", {"request": request, "character_id": character_id, "note": None}
    )


@router.get("/add-trigger")
def add_trigger(request: Request, character_id: int):
    return templates.TemplateResponse(
        "notes/_add_trigger.html", {"request": request, "character_id": character_id}
    )


@router.post("")
def create_note(
    character_id: int,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
    title: str = Form(""),
    body: str = Form(""),
):
    note = Note(character_id=character_id, title=title or None, body=body or None)
    db.add(note)
    db.commit()
    db.refresh(note)
    row_html = render_fragment("notes/_row.html", character_id=character_id, note=note)
    trigger_html = render_fragment("notes/_add_trigger.html", character_id=character_id)
    return HTMLResponse(row_html + trigger_html)


@router.get("/{note_id}")
def show_note(request: Request, character_id: int, note_id: int, db: Session = Depends(get_db)):
    note = _get_or_404(character_id, note_id, db)
    return templates.TemplateResponse(
        "notes/_row.html", {"request": request, "character_id": character_id, "note": note}
    )


@router.get("/{note_id}/edit")
def edit_note(request: Request, character_id: int, note_id: int, db: Session = Depends(get_db)):
    note = _get_or_404(character_id, note_id, db)
    return templates.TemplateResponse(
        "notes/_row_edit.html", {"request": request, "character_id": character_id, "note": note}
    )


@router.put("/{note_id}")
def update_note(
    request: Request,
    character_id: int,
    note_id: int,
    db: Session = Depends(get_db),
    title: str = Form(""),
    body: str = Form(""),
):
    note = _get_or_404(character_id, note_id, db)
    note.title = title or None
    note.body = body or None
    db.commit()
    return templates.TemplateResponse(
        "notes/_row.html", {"request": request, "character_id": character_id, "note": note}
    )


@router.delete("/{note_id}")
def delete_note(character_id: int, note_id: int, db: Session = Depends(get_db)):
    note = _get_or_404(character_id, note_id, db)
    db.delete(note)
    db.commit()
    return HTMLResponse("")
