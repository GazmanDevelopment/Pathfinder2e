from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_character_or_404
from app.models import Character, Spell
from app.templating import render_fragment, templates

router = APIRouter(prefix="/characters/{character_id}/spells", tags=["spells"])


def _get_or_404(character_id: int, spell_id: int, db: Session) -> Spell:
    spell = db.get(Spell, spell_id)
    if spell is None or spell.character_id != character_id:
        raise HTTPException(status_code=404, detail="Spell not found")
    return spell


def _int_or_none(value: str):
    return int(value) if value.strip() else None


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
):
    spell.name = name
    spell.rank = rank or None
    spell.uses = uses or None
    spell.action_cost = action_cost or None
    spell.range = range or None
    spell.effect = effect or None
    spell.flags = flags or None
    spell.attack_bonus = _int_or_none(attack_bonus)
    spell.damage_formula = damage_formula or None


@router.get("/new")
def new_spell(request: Request, character_id: int, character: Character = Depends(get_character_or_404)):
    return templates.TemplateResponse(
        "spells/_row_edit.html", {"request": request, "character_id": character_id, "spell": None}
    )


@router.get("/add-trigger")
def add_trigger(request: Request, character_id: int):
    return templates.TemplateResponse(
        "spells/_add_trigger.html", {"request": request, "character_id": character_id}
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
):
    spell = Spell(character_id=character_id, name=name)
    _apply_form(spell, name, rank, uses, action_cost, range, effect, flags, attack_bonus, damage_formula)
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
def edit_spell(request: Request, character_id: int, spell_id: int, db: Session = Depends(get_db)):
    spell = _get_or_404(character_id, spell_id, db)
    return templates.TemplateResponse(
        "spells/_row_edit.html", {"request": request, "character_id": character_id, "spell": spell}
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
):
    spell = _get_or_404(character_id, spell_id, db)
    _apply_form(spell, name, rank, uses, action_cost, range, effect, flags, attack_bonus, damage_formula)
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
