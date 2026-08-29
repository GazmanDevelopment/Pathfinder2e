import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.auth import NotAuthenticated, require_login
from app.config import SESSION_HTTPS_ONLY, SESSION_SECRET, UPLOAD_DIR
from app.db import Base, engine
from app.routers import auth, avatar, characters, equipment, features, notes, proficiencies, spells
from app.templating import templates

if not SESSION_SECRET:
    raise RuntimeError(
        "SESSION_SECRET is not set. Auth is always on, so a session signing "
        "secret is required. Set the SESSION_SECRET environment variable."
    )

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger("app")

# Directories must exist before StaticFiles mounts below, and before the
# lifespan's create_all() runs against the SQLite file path.
(BASE_DIR.parent / "data").mkdir(parents=True, exist_ok=True)
(UPLOAD_DIR / "avatars").mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Character Sheet", lifespan=lifespan)

# `lax` lets the session cookie survive the top-level redirect back from the
# IdP; authlib also stashes its transient state/nonce in this session.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=SESSION_HTTPS_ONLY,
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# Auth routes are open; every sheet route requires a logged-in session. The
# gating lives here so no individual router file has to know about auth.
app.include_router(auth.router)

_protected = [Depends(require_login)]
app.include_router(characters.router, dependencies=_protected)
app.include_router(proficiencies.router, dependencies=_protected)
app.include_router(spells.router, dependencies=_protected)
app.include_router(equipment.router, dependencies=_protected)
app.include_router(features.router, dependencies=_protected)
app.include_router(notes.router, dependencies=_protected)
app.include_router(avatar.router, dependencies=_protected)


ERROR_HEADINGS = {
    400: "Bad request",
    403: "Not allowed",
    404: "Not found",
    405: "Not allowed here",
    413: "Too large",
    422: "That didn't look right",
    500: "Something went wrong",
}

# Shown instead of the raw exception text, which can be terse or leak
# internals. Keyed by status; anything unlisted falls back to the detail.
ERROR_MESSAGES = {
    404: "That page or character doesn't exist. It may have been deleted.",
    405: "That address doesn't accept this kind of request.",
    422: "Some of the values submitted weren't valid. Go back and try again.",
    500: "An unexpected error occurred. The details have been logged.",
}


def render_error(request: Request, status: int, detail: str, json_detail=None):
    """Errors as a page for browsers, JSON for everything else.

    HTMX requests land here too, but htmx won't swap a non-2xx body by
    default, so they fall through to the JSON branch harmlessly.
    """
    if "text/html" not in request.headers.get("accept", ""):
        return JSONResponse(
            {"detail": json_detail if json_detail is not None else detail},
            status_code=status,
        )

    return templates.TemplateResponse(
        "error.html",
        {
            "request": request,
            "status": status,
            "heading": ERROR_HEADINGS.get(status, "Error"),
            "detail": ERROR_MESSAGES.get(status, detail),
        },
        status_code=status,
    )


@app.exception_handler(StarletteHTTPException)
def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """404s, 405s, and any explicit HTTPException raised by a route.

    A character can be deleted while someone still has its URL open or
    bookmarked, which makes 404 the most likely error this app produces.
    """
    return render_error(request, exc.status_code, str(exc.detail))


@app.exception_handler(RequestValidationError)
def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Malformed form or query data — e.g. a hand-edited request."""
    return render_error(request, 422, "Invalid request data", json_detail=exc.errors())


@app.exception_handler(NotAuthenticated)
def not_authenticated_handler(request: Request, exc: NotAuthenticated):
    """No session user. Bounce browsers to /login (preserving where they were),
    tell htmx to redirect client-side, and give API clients a plain 401."""
    if request.headers.get("HX-Request") == "true":
        resp = Response(status_code=200)
        resp.headers["HX-Redirect"] = "/login"
        return resp
    if "text/html" in request.headers.get("accept", ""):
        target = "/login?" + urlencode({"next": request.url.path})
        return RedirectResponse(url=target, status_code=303)
    return JSONResponse({"detail": "Not authenticated"}, status_code=401)


@app.exception_handler(Exception)
def unhandled_exception_handler(request: Request, exc: Exception):
    """Last resort, so an unexpected crash is still a readable page.

    Starlette re-raises after this returns, so uvicorn still logs the full
    traceback — the page deliberately shows none of it.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return render_error(request, 500, "Internal server error")


@app.get("/")
def index():
    return RedirectResponse(url="/characters")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
