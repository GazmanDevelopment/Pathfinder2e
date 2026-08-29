from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import UPLOAD_DIR
from app.db import Base, engine
from app.routers import avatar, characters, equipment, features, notes, proficiencies, spells
from app.templating import templates

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


ERROR_HEADINGS = {
    404: "Not found",
    403: "Not allowed",
    500: "Something went wrong",
}


@app.exception_handler(StarletteHTTPException)
def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Render errors as a page for browsers, keeping JSON for API clients.

    A character can be deleted while someone still has its URL open (or
    bookmarked), so a bare JSON body is a dead end for the most likely
    404 this app produces.
    """
    wants_html = "text/html" in request.headers.get("accept", "")
    if not wants_html:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status": exc.status_code,
            "heading": ERROR_HEADINGS.get(exc.status_code, "Error"),
            "detail": exc.detail,
        },
        status_code=exc.status_code,
    )


@app.get("/")
def index():
    return RedirectResponse(url="/characters")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
