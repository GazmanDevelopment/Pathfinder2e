from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_character_or_404
from app.models import Character, Equipment, ReferenceLibrary
from app.reference_library import search_reference
from app.templating import clean_rich_text, render_fragment, templates

router = APIRouter(prefix="/characters/{character_id}/equipment", tags=["equipment"])


def _get_or_404(character_id: int, item_id: int, db: Session) -> Equipment:
    item = db.get(Equipment, item_id)
    if item is None or item.character_id != character_id:
        raise HTTPException(status_code=404, detail="Equipment item not found")
    return item


def _int_or_none(value: str):
    return int(value) if value.strip() else None


def _get_reference_or_none(db: Session, reference_id: int | None) -> ReferenceLibrary | None:
    if reference_id is None:
        return None
    entry = db.get(ReferenceLibrary, reference_id)
    if entry is not None and entry.entry_type != "equipment":
        return None
    return entry


def _apply_form(
    item: Equipment,
    name: str,
    description: str,
    notes: str,
    qty: str,
    container: str,
    attack_bonus: str,
    damage_formula: str,
    agile: bool,
    reference_id: str,
    reference_version: str,
):
    item.name = name
    # description stays plain text — always hand-typed, never prefilled from
    # the reference library (it's the short "+1, 1d6 fire"-style tag shown
    # inline, rendered escaped, not through the rich_text filter). notes is
    # the long-form field that carries formatting.
    item.description = description or None
    item.notes = clean_rich_text(notes)
    item.qty = _int_or_none(qty)
    item.container = container or None
    item.attack_bonus = _int_or_none(attack_bonus)
    item.damage_formula = damage_formula or None
    item.agile = agile
    item.reference_id = _int_or_none(reference_id)
    item.reference_version = reference_version or None


@router.get("/new")
def new_equipment(
    request: Request,
    character_id: int,
    reference_id: int | None = None,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
):
    prefill = _get_reference_or_none(db, reference_id)
    return templates.TemplateResponse(
        "equipment/_row_edit.html",
        {"request": request, "character_id": character_id, "item": None, "prefill": prefill},
    )


@router.get("/add-trigger")
def add_trigger(request: Request, character_id: int):
    return templates.TemplateResponse(
        "equipment/_add_trigger.html", {"request": request, "character_id": character_id}
    )


@router.get("/reference-search")
def reference_search(request: Request, character_id: int, q: str = "", db: Session = Depends(get_db)):
    results = search_reference(db, "equipment", q)
    return templates.TemplateResponse(
        "_reference_results.html",
        {"request": request, "character_id": character_id, "resource": "equipment", "results": results},
    )


@router.post("")
def create_equipment(
    character_id: int,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
    name: str = Form(...),
    description: str = Form(""),
    notes: str = Form(""),
    qty: str = Form("1"),
    container: str = Form(""),
    attack_bonus: str = Form(""),
    damage_formula: str = Form(""),
    agile: bool = Form(False),
    reference_id: str = Form(""),
    reference_version: str = Form(""),
):
    item = Equipment(character_id=character_id, name=name)
    _apply_form(
        item,
        name,
        description,
        notes,
        qty,
        container,
        attack_bonus,
        damage_formula,
        agile,
        reference_id,
        reference_version,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    row_html = render_fragment("equipment/_row.html", character_id=character_id, item=item)
    trigger_html = render_fragment("equipment/_add_trigger.html", character_id=character_id)
    return HTMLResponse(row_html + trigger_html)


@router.get("/{item_id}")
def show_equipment(request: Request, character_id: int, item_id: int, db: Session = Depends(get_db)):
    item = _get_or_404(character_id, item_id, db)
    return templates.TemplateResponse(
        "equipment/_row.html", {"request": request, "character_id": character_id, "item": item}
    )


@router.get("/{item_id}/edit")
def edit_equipment(
    request: Request,
    character_id: int,
    item_id: int,
    refresh_from_reference: bool = False,
    db: Session = Depends(get_db),
):
    item = _get_or_404(character_id, item_id, db)
    prefill = _get_reference_or_none(db, item.reference_id) if refresh_from_reference else None
    return templates.TemplateResponse(
        "equipment/_row_edit.html",
        {"request": request, "character_id": character_id, "item": item, "prefill": prefill},
    )


@router.put("/{item_id}")
def update_equipment(
    request: Request,
    character_id: int,
    item_id: int,
    db: Session = Depends(get_db),
    name: str = Form(...),
    description: str = Form(""),
    notes: str = Form(""),
    qty: str = Form("1"),
    container: str = Form(""),
    attack_bonus: str = Form(""),
    damage_formula: str = Form(""),
    agile: bool = Form(False),
    reference_id: str = Form(""),
    reference_version: str = Form(""),
):
    item = _get_or_404(character_id, item_id, db)
    _apply_form(
        item,
        name,
        description,
        notes,
        qty,
        container,
        attack_bonus,
        damage_formula,
        agile,
        reference_id,
        reference_version,
    )
    db.commit()
    return templates.TemplateResponse(
        "equipment/_row.html", {"request": request, "character_id": character_id, "item": item}
    )


@router.delete("/{item_id}")
def delete_equipment(character_id: int, item_id: int, db: Session = Depends(get_db)):
    item = _get_or_404(character_id, item_id, db)
    db.delete(item)
    db.commit()
    return HTMLResponse("")
