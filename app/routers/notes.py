from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_character_or_404
from app.models import Character, Note
from app.templating import clean_rich_text, render_fragment, templates

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
    note = Note(character_id=character_id, title=title or None, body=clean_rich_text(body))
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
    note.body = clean_rich_text(body)
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


@router.post("/{note_id}/pin")
def toggle_pin_note(
    request: Request,
    character_id: int,
    note_id: int,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
):
    """Toggles a note's pinned state (issue #46) and re-renders the whole
    list in its new order. Pinning moves a note's position, so — unlike
    edit/delete, which only ever change or remove a row in place — a
    single-row swap can't relocate it; only re-rendering all of #note-list
    actually moves it. character.notes' relationship order_by (see
    models.py) does the actual pinned-first sorting; this just re-reads it
    after the commit, since it wasn't accessed earlier in this request.
    """
    note = _get_or_404(character_id, note_id, db)
    note.is_pinned = not note.is_pinned
    db.commit()
    return templates.TemplateResponse(
        "notes/_list_items.html",
        {"request": request, "character_id": character_id, "character": character},
    )
