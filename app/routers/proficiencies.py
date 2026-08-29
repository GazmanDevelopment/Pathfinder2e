from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_character_or_404
from app.models import Character, Proficiency
from app.templating import render_fragment, templates

router = APIRouter(prefix="/characters/{character_id}/proficiencies", tags=["proficiencies"])


def _get_or_404(character_id: int, prof_id: int, db: Session) -> Proficiency:
    prof = db.get(Proficiency, prof_id)
    if prof is None or prof.character_id != character_id:
        raise HTTPException(status_code=404, detail="Proficiency not found")
    return prof


def _int_or_none(value: str):
    return int(value) if value.strip() else None


@router.get("/new")
def new_proficiency(
    request: Request, character_id: int, character: Character = Depends(get_character_or_404)
):
    return templates.TemplateResponse(
        "proficiencies/_row_edit.html", {"request": request, "character_id": character_id, "prof": None}
    )


@router.get("/add-trigger")
def add_trigger(request: Request, character_id: int):
    return templates.TemplateResponse(
        "proficiencies/_add_trigger.html", {"request": request, "character_id": character_id}
    )


@router.post("")
def create_proficiency(
    character_id: int,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
    name: str = Form(...),
    rank: str = Form(""),
    bonus: str = Form(""),
):
    prof = Proficiency(character_id=character_id, name=name, rank=rank or None, bonus=_int_or_none(bonus))
    db.add(prof)
    db.commit()
    db.refresh(prof)
    row_html = render_fragment("proficiencies/_row.html", character_id=character_id, prof=prof)
    trigger_html = render_fragment("proficiencies/_add_trigger.html", character_id=character_id)
    return HTMLResponse(row_html + trigger_html)


@router.get("/{prof_id}")
def show_proficiency(request: Request, character_id: int, prof_id: int, db: Session = Depends(get_db)):
    prof = _get_or_404(character_id, prof_id, db)
    return templates.TemplateResponse(
        "proficiencies/_row.html", {"request": request, "character_id": character_id, "prof": prof}
    )


@router.get("/{prof_id}/edit")
def edit_proficiency(request: Request, character_id: int, prof_id: int, db: Session = Depends(get_db)):
    prof = _get_or_404(character_id, prof_id, db)
    return templates.TemplateResponse(
        "proficiencies/_row_edit.html", {"request": request, "character_id": character_id, "prof": prof}
    )


@router.put("/{prof_id}")
def update_proficiency(
    request: Request,
    character_id: int,
    prof_id: int,
    db: Session = Depends(get_db),
    name: str = Form(...),
    rank: str = Form(""),
    bonus: str = Form(""),
):
    prof = _get_or_404(character_id, prof_id, db)
    prof.name = name
    prof.rank = rank or None
    prof.bonus = _int_or_none(bonus)
    db.commit()
    return templates.TemplateResponse(
        "proficiencies/_row.html", {"request": request, "character_id": character_id, "prof": prof}
    )


@router.delete("/{prof_id}")
def delete_proficiency(character_id: int, prof_id: int, db: Session = Depends(get_db)):
    prof = _get_or_404(character_id, prof_id, db)
    db.delete(prof)
    db.commit()
    return HTMLResponse("")
