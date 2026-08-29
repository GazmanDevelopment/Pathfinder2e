import io
import uuid

from fastapi import APIRouter, Depends, Request, UploadFile
from PIL import Image, ImageOps
from sqlalchemy.orm import Session

from app.config import AVATAR_MAX_DIM, AVATAR_THUMB_DIM, MAX_AVATAR_BYTES, UPLOAD_DIR
from app.db import get_db
from app.deps import get_character_or_404
from app.models import Character
from app.templating import templates

router = APIRouter(prefix="/characters/{character_id}/avatar", tags=["avatar"])

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
AVATAR_DIR = UPLOAD_DIR / "avatars"


def _avatar_error(request: Request, character: Character, message: str):
    return templates.TemplateResponse(
        "characters/_avatar.html", {"request": request, "character": character, "error": message}
    )


@router.post("")
async def upload_avatar(
    request: Request,
    file: UploadFile,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
):
    contents = await file.read()

    if len(contents) == 0:
        return _avatar_error(request, character, "No file was selected.")
    if len(contents) > MAX_AVATAR_BYTES:
        return _avatar_error(request, character, "Image is too large (max 8 MB).")

    try:
        img = Image.open(io.BytesIO(contents))
        img.load()
    except Exception:
        return _avatar_error(request, character, "That file isn't a readable image.")

    if img.format not in ALLOWED_FORMATS:
        return _avatar_error(request, character, "Only JPEG, PNG, and WEBP images are supported.")

    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")

    main_img = img.copy()
    main_img.thumbnail((AVATAR_MAX_DIM, AVATAR_MAX_DIM))
    thumb_img = img.copy()
    thumb_img.thumbnail((AVATAR_THUMB_DIM, AVATAR_THUMB_DIM))

    token = uuid.uuid4().hex
    main_name = f"{token}.jpg"
    thumb_name = f"{token}_thumb.jpg"

    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    main_img.save(AVATAR_DIR / main_name, format="JPEG", quality=85)
    thumb_img.save(AVATAR_DIR / thumb_name, format="JPEG", quality=85)

    old_main = character.avatar_path
    old_thumb = character.avatar_thumb_path

    character.avatar_path = f"avatars/{main_name}"
    character.avatar_thumb_path = f"avatars/{thumb_name}"
    db.commit()

    for old in (old_main, old_thumb):
        if old:
            (UPLOAD_DIR / old).unlink(missing_ok=True)

    return templates.TemplateResponse(
        "characters/_avatar.html", {"request": request, "character": character}
    )


@router.delete("")
def delete_avatar(
    request: Request,
    character: Character = Depends(get_character_or_404),
    db: Session = Depends(get_db),
):
    old_main = character.avatar_path
    old_thumb = character.avatar_thumb_path
    character.avatar_path = None
    character.avatar_thumb_path = None
    db.commit()

    for old in (old_main, old_thumb):
        if old:
            (UPLOAD_DIR / old).unlink(missing_ok=True)

    return templates.TemplateResponse(
        "characters/_avatar.html", {"request": request, "character": character}
    )
