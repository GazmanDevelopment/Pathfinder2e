from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_character_or_404
from app.models import Character, ReferenceLibrary, Spell
from app.reference_library import search_reference
from app.templating import clean_rich_text, render_fragment, templates

router = APIRouter(prefix="/characters/{character_id}/spells", tags=["spells"])


def _get_or_404(character_id: int, spell_id: int, db: Session) -> Spell:
    spell = db.get(Spell, spell_id)
    if spell is None or spell.character_id != character_id:
        raise HTTPException(status_code=404, detail="Spell not found")
    return spell


def _int_or_none(value: str):
    return int(value) if value.strip() else None


def _get_reference_or_none(db: Session, reference_id: int | None) -> ReferenceLibrary | None:
    if reference_id is None:
        return None
    entry = db.get(ReferenceLibrary, reference_id)
    # A mismatched entry_type means a stale/foreign id (e.g. a URL edited by
    # hand) — ignore it rather than 404 the whole page over it.
    if entry is not None and entry.entry_type != "spell":
        return None
    return entry


def _apply_form(
    spell: Spell,
    name: str,
    rank: str,
    uses: str,
    action_cost: str,
    range: str,
    effect: str,
    flags: str,
    attack_bonus: str,
    damage_formula: str,
    reference_id: str,
    reference_version: str,
):
    spell.name = name
    spell.rank = rank or None
    spell.uses = uses or None
    spell.action_cost = action_cost or None
    spell.range = range or None
    spell.effect = clean_rich_text(effect)
    spell.flags = flags or None
    spell.attack_bonus = _int_or_none(attack_bonus)
    spell.damage_formula = damage_formula or None
    spell.reference_id = _int_or_none(reference_id)
    spell.reference_version = reference_version or None


@router.get("/new")
def new_spell(
    request: Request,
    character_id: int,
    reference_id: int | None = None,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
):
    prefill = _get_reference_or_none(db, reference_id)
    return templates.TemplateResponse(
        "spells/_row_edit.html",
        {"request": request, "character_id": character_id, "spell": None, "prefill": prefill},
    )


@router.get("/add-trigger")
def add_trigger(request: Request, character_id: int):
    return templates.TemplateResponse(
        "spells/_add_trigger.html", {"request": request, "character_id": character_id}
    )


@router.get("/reference-search")
def reference_search(request: Request, character_id: int, q: str = "", db: Session = Depends(get_db)):
    results = search_reference(db, "spell", q)
    return templates.TemplateResponse(
        "_reference_results.html",
        {"request": request, "character_id": character_id, "resource": "spells", "results": results},
    )


@router.post("")
def create_spell(
    character_id: int,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
    name: str = Form(...),
    rank: str = Form(""),
    uses: str = Form(""),
    action_cost: str = Form(""),
    range: str = Form(""),
    effect: str = Form(""),
    flags: str = Form(""),
    attack_bonus: str = Form(""),
    damage_formula: str = Form(""),
    reference_id: str = Form(""),
    reference_version: str = Form(""),
):
    spell = Spell(character_id=character_id, name=name)
    _apply_form(
        spell,
        name,
        rank,
        uses,
        action_cost,
        range,
        effect,
        flags,
        attack_bonus,
        damage_formula,
        reference_id,
        reference_version,
    )
    db.add(spell)
    db.commit()
    db.refresh(spell)
    row_html = render_fragment("spells/_row.html", character_id=character_id, spell=spell)
    trigger_html = render_fragment("spells/_add_trigger.html", character_id=character_id)
    return HTMLResponse(row_html + trigger_html)


@router.get("/{spell_id}")
def show_spell(request: Request, character_id: int, spell_id: int, db: Session = Depends(get_db)):
    spell = _get_or_404(character_id, spell_id, db)
    return templates.TemplateResponse(
        "spells/_row.html", {"request": request, "character_id": character_id, "spell": spell}
    )


@router.get("/{spell_id}/edit")
def edit_spell(
    request: Request,
    character_id: int,
    spell_id: int,
    refresh_from_reference: bool = False,
    db: Session = Depends(get_db),
):
    spell = _get_or_404(character_id, spell_id, db)
    prefill = _get_reference_or_none(db, spell.reference_id) if refresh_from_reference else None
    return templates.TemplateResponse(
        "spells/_row_edit.html",
        {"request": request, "character_id": character_id, "spell": spell, "prefill": prefill},
    )


@router.put("/{spell_id}")
def update_spell(
    request: Request,
    character_id: int,
    spell_id: int,
    db: Session = Depends(get_db),
    name: str = Form(...),
    rank: str = Form(""),
    uses: str = Form(""),
    action_cost: str = Form(""),
    range: str = Form(""),
    effect: str = Form(""),
    flags: str = Form(""),
    attack_bonus: str = Form(""),
    damage_formula: str = Form(""),
    reference_id: str = Form(""),
    reference_version: str = Form(""),
):
    spell = _get_or_404(character_id, spell_id, db)
    _apply_form(
        spell,
        name,
        rank,
        uses,
        action_cost,
        range,
        effect,
        flags,
        attack_bonus,
        damage_formula,
        reference_id,
        reference_version,
    )
    db.commit()
    return templates.TemplateResponse(
        "spells/_row.html", {"request": request, "character_id": character_id, "spell": spell}
    )


@router.delete("/{spell_id}")
def delete_spell(character_id: int, spell_id: int, db: Session = Depends(get_db)):
    spell = _get_or_404(character_id, spell_id, db)
    db.delete(spell)
    db.commit()
    return HTMLResponse("")
