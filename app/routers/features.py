from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_character_or_404
from app.models import Character, Feature, ReferenceLibrary
from app.reference_library import search_reference
from app.templating import clean_rich_text, render_fragment, templates

router = APIRouter(prefix="/characters/{character_id}/features", tags=["features"])


def _get_or_404(character_id: int, feature_id: int, db: Session) -> Feature:
    feature = db.get(Feature, feature_id)
    if feature is None or feature.character_id != character_id:
        raise HTTPException(status_code=404, detail="Feature not found")
    return feature


def _int_or_none(value: str):
    return int(value) if value.strip() else None


def _next_sort_order(character_id: int, db: Session) -> int:
    """See spells.py's identical helper — same issue #48 pattern."""
    max_order = db.query(func.max(Feature.sort_order)).filter(Feature.character_id == character_id).scalar()
    return (max_order or 0) + 1


def _get_reference_or_none(db: Session, reference_id: int | None) -> ReferenceLibrary | None:
    if reference_id is None:
        return None
    entry = db.get(ReferenceLibrary, reference_id)
    if entry is not None and entry.entry_type != "feature":
        return None
    return entry


def _apply_form(
    feature: Feature,
    source: str,
    name: str,
    effect: str,
    level_gained: str,
    reference_id: str,
    reference_version: str,
):
    feature.source = source or None
    feature.name = name
    feature.effect = clean_rich_text(effect)
    feature.level_gained = _int_or_none(level_gained)
    feature.reference_id = _int_or_none(reference_id)
    feature.reference_version = reference_version or None


@router.get("/new")
def new_feature(
    request: Request,
    character_id: int,
    reference_id: int | None = None,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
):
    prefill = _get_reference_or_none(db, reference_id)
    return templates.TemplateResponse(
        "features/_row_edit.html",
        {"request": request, "character_id": character_id, "feature": None, "prefill": prefill},
    )


@router.get("/add-trigger")
def add_trigger(request: Request, character_id: int):
    return templates.TemplateResponse(
        "features/_add_trigger.html", {"request": request, "character_id": character_id}
    )


@router.get("/reference-search")
def reference_search(request: Request, character_id: int, q: str = "", db: Session = Depends(get_db)):
    results = search_reference(db, "feature", q)
    return templates.TemplateResponse(
        "_reference_results.html",
        {"request": request, "character_id": character_id, "resource": "features", "results": results},
    )


@router.post("")
def create_feature(
    character_id: int,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
    source: str = Form(""),
    name: str = Form(...),
    effect: str = Form(""),
    level_gained: str = Form(""),
    reference_id: str = Form(""),
    reference_version: str = Form(""),
):
    feature = Feature(
        character_id=character_id, name=name, sort_order=_next_sort_order(character_id, db)
    )
    _apply_form(feature, source, name, effect, level_gained, reference_id, reference_version)
    db.add(feature)
    db.commit()
    db.refresh(feature)
    row_html = render_fragment("features/_row.html", character_id=character_id, feature=feature)
    trigger_html = render_fragment("features/_add_trigger.html", character_id=character_id)
    return HTMLResponse(row_html + trigger_html)


@router.get("/{feature_id}")
def show_feature(request: Request, character_id: int, feature_id: int, db: Session = Depends(get_db)):
    feature = _get_or_404(character_id, feature_id, db)
    return templates.TemplateResponse(
        "features/_row.html", {"request": request, "character_id": character_id, "feature": feature}
    )


@router.get("/{feature_id}/edit")
def edit_feature(
    request: Request,
    character_id: int,
    feature_id: int,
    refresh_from_reference: bool = False,
    db: Session = Depends(get_db),
):
    feature = _get_or_404(character_id, feature_id, db)
    prefill = _get_reference_or_none(db, feature.reference_id) if refresh_from_reference else None
    return templates.TemplateResponse(
        "features/_row_edit.html",
        {"request": request, "character_id": character_id, "feature": feature, "prefill": prefill},
    )


@router.put("/{feature_id}")
def update_feature(
    request: Request,
    character_id: int,
    feature_id: int,
    db: Session = Depends(get_db),
    source: str = Form(""),
    name: str = Form(...),
    effect: str = Form(""),
    level_gained: str = Form(""),
    reference_id: str = Form(""),
    reference_version: str = Form(""),
):
    feature = _get_or_404(character_id, feature_id, db)
    _apply_form(feature, source, name, effect, level_gained, reference_id, reference_version)
    db.commit()
    return templates.TemplateResponse(
        "features/_row.html", {"request": request, "character_id": character_id, "feature": feature}
    )


@router.delete("/{feature_id}")
def delete_feature(character_id: int, feature_id: int, db: Session = Depends(get_db)):
    feature = _get_or_404(character_id, feature_id, db)
    db.delete(feature)
    db.commit()
    return HTMLResponse("")


@router.post("/{feature_id}/move")
def move_feature(
    request: Request,
    character_id: int,
    feature_id: int,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
    direction: str = Form(...),
):
    """See spells.py's move_spell — same issue #48 pattern."""
    features = character.features
    idx = next((i for i, f in enumerate(features) if f.id == feature_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Feature not found")
    swap_idx = idx - 1 if direction == "up" else idx + 1 if direction == "down" else None
    if swap_idx is not None and 0 <= swap_idx < len(features):
        features[idx].sort_order, features[swap_idx].sort_order = (
            features[swap_idx].sort_order,
            features[idx].sort_order,
        )
        db.commit()
    return templates.TemplateResponse(
        "features/_list_items.html",
        {"request": request, "character_id": character_id, "character": character},
    )
