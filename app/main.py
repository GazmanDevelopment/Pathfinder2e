from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import UPLOAD_DIR
from app.db import Base, engine
from app.routers import avatar, characters, equipment, features, notes, proficiencies, spells

BASE_DIR = Path(__file__).resolve().parent

# Directories must exist before StaticFiles mounts below, and before the
# lifespan's create_all() runs against the SQLite file path.
(BASE_DIR.parent / "data").mkdir(parents=True, exist_ok=True)
(UPLOAD_DIR / "avatars").mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Character Sheet", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

app.include_router(characters.router)
app.include_router(proficiencies.router)
app.include_router(spells.router)
app.include_router(equipment.router)
app.include_router(features.router)
app.include_router(notes.router)
app.include_router(avatar.router)


@app.get("/")
def index():
    return RedirectResponse(url="/characters")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
