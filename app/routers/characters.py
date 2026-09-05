import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import ai_levelup_configured
from app.db import get_db
from app.deps import get_character_or_404, get_writable_character
from app.models import Character, Proficiency, User
from app.pathbuilder import PathbuilderImportError, build_export_payload, fetch_build, apply_import
from app.seed_data import DEFAULT_PROFICIENCY_NAMES
from app.templating import templates

router = APIRouter(prefix="/characters", tags=["characters"])


@router.get("")
def list_characters(
    request: Request, owner_id: int | None = None, q: str | None = None, db: Session = Depends(get_db)
):
    user = request.session["user"]
    is_admin = user["role"] == "admin"
    # Archives (Phase 8) are ordinary characters rows kept only as read-only
    # history off their live character — never a separate pickable character
    # in their own right, so they never appear in any of this route's lists.
    query = db.query(Character).filter(Character.is_archived.is_(False)).order_by(Character.id)
    viewing_owner = None

    if not is_admin:
        query = query.filter(Character.user_id == user["id"])
    elif owner_id is not None:
        # Phase 4b: admin drilling into one (typically disabled) user's
        # characters from /admin/users, e.g. to see what a departed player
        # had before deciding whether to delete their account.
        viewing_owner = db.get(User, owner_id)
        if viewing_owner is None:
            raise HTTPException(status_code=404, detail="User not found")
        query = query.filter(Character.user_id == owner_id)
    else:
        # Default admin view: a disabled user's characters aren't deleted or
        # reassigned, just filtered out here — same as Phase 4b's users list.
        query = query.join(User, Character.user_id == User.id).filter(User.is_disabled.is_(False))
        if q and q.strip():
            # Issue #35: one combined search across both the character's own
            # name and its owner's name/email — this is the one view where
            # an admin browses every character across every player at once,
            # so a single box covering both fields is more useful than
            # separate searches split across two pages.
            pattern = f"%{q.strip()}%"
            query = query.filter(
                or_(
                    Character.name.ilike(pattern),
                    User.display_name.ilike(pattern),
                    User.email.ilike(pattern),
                )
            )

    characters = query.all()
    return templates.TemplateResponse(
        "characters/list.html",
        {
            "request": request,
            "characters": characters,
            "is_admin_view": is_admin and viewing_owner is None,
            "viewing_owner": viewing_owner,
            "q": q or "",
        },
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


@router.post("/import/pathbuilder")
def import_pathbuilder(request: Request, build_id: str = Form(...), db: Session = Depends(get_db)):
    """Creates a new character from a Pathbuilder 2e export. See
    app/pathbuilder.py for the field mapping and what's deliberately left
    blank rather than guessed at.
    """
    try:
        build = fetch_build(build_id)
        character = Character(user_id=request.session["user"]["id"])
        rows = apply_import(character, build, build_id)
    except PathbuilderImportError as exc:
        user = request.session["user"]
        is_admin = user["role"] == "admin"
        query = db.query(Character).filter(Character.is_archived.is_(False)).order_by(Character.id)
        if not is_admin:
            query = query.filter(Character.user_id == user["id"])
        else:
            query = query.join(User, Character.user_id == User.id).filter(User.is_disabled.is_(False))
        return templates.TemplateResponse(
            "characters/list.html",
            {
                "request": request,
                "characters": query.all(),
                "is_admin_view": is_admin,
                "viewing_owner": None,
                "import_error": str(exc),
                "q": "",
            },
        )

    db.add(character)
    db.flush()
    for row in rows:
        row.character_id = character.id
        db.add(row)
    db.commit()
    return RedirectResponse(url=f"/characters/{character.id}", status_code=303)


@router.get("/{character_id}")
def character_sheet(
    request: Request, character: Character = Depends(get_character_or_404), db: Session = Depends(get_db)
):
    # read_only is set ONCE here and relied on to propagate through every
    # nested {% include %} below it (none of them use "without context") —
    # every mutating-control template checks it, but the actual security
    # boundary is get_writable_character (Phase 8), not this flag; this is
    # only the matching UI treatment so a read-only sheet doesn't show
    # controls that would just 403 anyway.
    read_only = character.is_archived
    ai_levelup_available = (
        not read_only
        and ai_levelup_configured()
        and (character.owner.can_use_ai_levelup or character.owner.role == "admin")
    )
    archives = (
        db.query(Character)
        .filter(Character.parent_character_id == character.id, Character.is_archived.is_(True))
        .order_by(Character.created_at.desc())
        .all()
        if not read_only
        else []
    )
    return templates.TemplateResponse(
        "characters/sheet.html",
        {
            "request": request,
            "character": character,
            "read_only": read_only,
            "ai_levelup_available": ai_levelup_available,
            "archives": archives,
        },
    )


@router.get("/{character_id}/export/pathbuilder")
def export_pathbuilder(character: Character = Depends(get_character_or_404)):
    payload = build_export_payload(character)
    filename = f"{(character.name or 'character').strip().replace(' ', '_')}_pathbuilder.json"
    return Response(
        content=json.dumps(payload, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/{character_id}")
def delete_character(character: Character = Depends(get_writable_character), db: Session = Depends(get_db)):
    # A live character's archives (Phase 8) reference it via
    # parent_character_id with no ON DELETE CASCADE — SQLite can't add that
    # to an existing column's FK without rebuilding the table, which isn't
    # worth doing for this — so leaving them in place would make this
    # delete fail outright (reproduced directly: sqlite3.IntegrityError,
    # "FOREIGN KEY constraint failed", not a graceful 400). Archives are
    # pure history of this specific character; once it's gone, so is its
    # history, matching the existing delete confirmation's own wording
    # ("This removes everything on their sheet").
    db.query(Character).filter(
        Character.parent_character_id == character.id, Character.is_archived.is_(True)
    ).delete(synchronize_session=False)
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
    character: Character = Depends(get_writable_character),
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
    # Renders only the inner body (issue #39) — the outer <details> shell is
    # rendered once by _core_stats.html at initial page load and never
    # touched again, so its open/closed state survives every subsequent
    # Cancel/HP-adjust/Save here.
    return templates.TemplateResponse(
        "characters/_core_stats_body.html", {"request": request, "character": character}
    )


@router.post("/{character_id}/hp/adjust")
def adjust_hp(
    request: Request,
    character: Character = Depends(get_writable_character),
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
        "characters/_core_stats_body.html", {"request": request, "character": character}
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
    character: Character = Depends(get_writable_character),
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
        "characters/_core_stats_body.html", {"request": request, "character": character}
    )


@router.get("/{character_id}/ability-scores")
def show_ability_scores(request: Request, character: Character = Depends(get_character_or_404)):
    # See show_core_stats' comment above — same issue #39 fix.
    return templates.TemplateResponse(
        "characters/_ability_scores_body.html", {"request": request, "character": character}
    )


@router.get("/{character_id}/ability-scores/edit")
def edit_ability_scores(request: Request, character: Character = Depends(get_character_or_404)):
    return templates.TemplateResponse(
        "characters/_ability_scores_edit.html", {"request": request, "character": character}
    )


@router.put("/{character_id}/ability-scores")
def save_ability_scores(
    request: Request,
    character: Character = Depends(get_writable_character),
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
        "characters/_ability_scores_body.html", {"request": request, "character": character}
    )


@router.get("/{character_id}/money")
def show_money(request: Request, character: Character = Depends(get_character_or_404)):
    # Split shell/body from the start (issue #47), same pattern issue #39
    # retrofitted onto core-stats/ability-scores: swapping only #money-body
    # (innerHTML) rather than the outer <details> means this section's
    # open/closed state is never disturbed by Edit/Save/Cancel.
    return templates.TemplateResponse(
        "characters/_money_body.html", {"request": request, "character": character}
    )


@router.get("/{character_id}/money/edit")
def edit_money(request: Request, character: Character = Depends(get_character_or_404)):
    return templates.TemplateResponse(
        "characters/_money_edit.html", {"request": request, "character": character}
    )


@router.put("/{character_id}/money")
def save_money(
    request: Request,
    character: Character = Depends(get_writable_character),
    db: Session = Depends(get_db),
    pp: str = Form(""),
    gp: str = Form(""),
    sp: str = Form(""),
    cp: str = Form(""),
):
    character.pp = _int_or_none(pp)
    character.gp = _int_or_none(gp)
    character.sp = _int_or_none(sp)
    character.cp = _int_or_none(cp)
    db.commit()
    return templates.TemplateResponse(
        "characters/_money_body.html", {"request": request, "character": character}
    )
