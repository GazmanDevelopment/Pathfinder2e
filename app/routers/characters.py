from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_character_or_404
from app.models import Character, Proficiency
from app.seed_data import DEFAULT_PROFICIENCY_NAMES
from app.templating import templates

router = APIRouter(prefix="/characters", tags=["characters"])


@router.get("")
def list_characters(request: Request, db: Session = Depends(get_db)):
    user = request.session["user"]
    query = db.query(Character).order_by(Character.id)
    if user["role"] != "admin":
        query = query.filter(Character.user_id == user["id"])
    characters = query.all()
    return templates.TemplateResponse(
        "characters/list.html",
        {"request": request, "characters": characters, "is_admin_view": user["role"] == "admin"},
    )


@router.post("")
def create_character(request: Request, name: str = Form(...), db: Session = Depends(get_db)):
    character = Character(name=name, user_id=request.session["user"]["id"])
    db.add(character)
    db.flush()
    for prof_name in DEFAULT_PROFICIENCY_NAMES:
        db.add(Proficiency(character_id=character.id, name=prof_name, rank="Untrained"))
    db.commit()
    return RedirectResponse(url=f"/characters/{character.id}", status_code=303)


@router.get("/{character_id}")
def character_sheet(
    request: Request, character: Character = Depends(get_character_or_404)
):
    return templates.TemplateResponse(
        "characters/sheet.html", {"request": request, "character": character}
    )


@router.delete("/{character_id}")
def delete_character(character: Character = Depends(get_character_or_404), db: Session = Depends(get_db)):
    db.delete(character)
    db.commit()
    return ""


@router.get("/{character_id}/header")
def show_header(request: Request, character: Character = Depends(get_character_or_404)):
    return templates.TemplateResponse(
        "characters/_header.html", {"request": request, "character": character}
    )


@router.get("/{character_id}/header/edit")
def edit_header(request: Request, character: Character = Depends(get_character_or_404)):
    return templates.TemplateResponse(
        "characters/_header_edit.html", {"request": request, "character": character}
    )


@router.put("/{character_id}/header")
def save_header(
    request: Request,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
    name: str = Form(...),
    ancestry: str = Form(""),
    character_class: str = Form(""),
    level: str = Form(""),
    size: str = Form(""),
    speed: str = Form(""),
    languages: str = Form(""),
    alignment: str = Form(""),
):
    character.name = name
    character.ancestry = ancestry or None
    character.character_class = character_class or None
    character.level = int(level) if level.strip() else None
    character.size = size or None
    character.speed = speed or None
    character.languages = languages or None
    character.alignment = alignment or None
    db.commit()
    return templates.TemplateResponse(
        "characters/_header.html", {"request": request, "character": character}
    )


@router.get("/{character_id}/core-stats")
def show_core_stats(request: Request, character: Character = Depends(get_character_or_404)):
    return templates.TemplateResponse(
        "characters/_core_stats.html", {"request": request, "character": character}
    )


@router.post("/{character_id}/hp/adjust")
def adjust_hp(
    request: Request,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
    delta: int = Form(...),
):
    """Nudge current HP by delta.

    Not clamped to 0 or to hp_max on purpose: this is a record of what the
    table decided, not a rules engine. Unset HP is treated as 0 so the first
    tap has somewhere to start from.
    """
    character.hp_current = (character.hp_current or 0) + delta
    db.commit()
    return templates.TemplateResponse(
        "characters/_core_stats.html", {"request": request, "character": character}
    )


@router.get("/{character_id}/core-stats/edit")
def edit_core_stats(request: Request, character: Character = Depends(get_character_or_404)):
    return templates.TemplateResponse(
        "characters/_core_stats_edit.html", {"request": request, "character": character}
    )


def _int_or_none(value: str):
    return int(value) if value.strip() else None


@router.put("/{character_id}/core-stats")
def save_core_stats(
    request: Request,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
    hp_current: str = Form(""),
    hp_max: str = Form(""),
    ac: str = Form(""),
    class_dc: str = Form(""),
    spell_dc: str = Form(""),
    spell_atk: str = Form(""),
    perception: str = Form(""),
    hero_points: str = Form(""),
):
    character.hp_current = _int_or_none(hp_current)
    character.hp_max = _int_or_none(hp_max)
    character.ac = _int_or_none(ac)
    character.class_dc = _int_or_none(class_dc)
    character.spell_dc = _int_or_none(spell_dc)
    character.spell_atk = _int_or_none(spell_atk)
    character.perception = _int_or_none(perception)
    character.hero_points = _int_or_none(hero_points)
    db.commit()
    return templates.TemplateResponse(
        "characters/_core_stats.html", {"request": request, "character": character}
    )


@router.get("/{character_id}/ability-scores")
def show_ability_scores(request: Request, character: Character = Depends(get_character_or_404)):
    return templates.TemplateResponse(
        "characters/_ability_scores.html", {"request": request, "character": character}
    )


@router.get("/{character_id}/ability-scores/edit")
def edit_ability_scores(request: Request, character: Character = Depends(get_character_or_404)):
    return templates.TemplateResponse(
        "characters/_ability_scores_edit.html", {"request": request, "character": character}
    )


@router.put("/{character_id}/ability-scores")
def save_ability_scores(
    request: Request,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
    str_score: str = Form(""),
    str_mod: str = Form(""),
    dex_score: str = Form(""),
    dex_mod: str = Form(""),
    con_score: str = Form(""),
    con_mod: str = Form(""),
    int_score: str = Form(""),
    int_mod: str = Form(""),
    wis_score: str = Form(""),
    wis_mod: str = Form(""),
    cha_score: str = Form(""),
    cha_mod: str = Form(""),
):
    character.str_score = _int_or_none(str_score)
    character.str_mod = _int_or_none(str_mod)
    character.dex_score = _int_or_none(dex_score)
    character.dex_mod = _int_or_none(dex_mod)
    character.con_score = _int_or_none(con_score)
    character.con_mod = _int_or_none(con_mod)
    character.int_score = _int_or_none(int_score)
    character.int_mod = _int_or_none(int_mod)
    character.wis_score = _int_or_none(wis_score)
    character.wis_mod = _int_or_none(wis_mod)
    character.cha_score = _int_or_none(cha_score)
    character.cha_mod = _int_or_none(cha_mod)
    db.commit()
    return templates.TemplateResponse(
        "characters/_ability_scores.html", {"request": request, "character": character}
    )
