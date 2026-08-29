from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import oauth, resolve_email, resolve_login_user, session_payload
from app.config import APP_BASE_URL, configured_providers
from app.db import get_db
from app.templating import templates

router = APIRouter(tags=["auth"])


def _provider_or_404(provider: str) -> dict:
    for cfg in configured_providers():
        if cfg["name"] == provider:
            return cfg
    raise HTTPException(status_code=404, detail="Unknown or unconfigured provider")


def _safe_next(request: Request, raw: str | None) -> str:
    """Only honour a same-origin, path-only next target."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/characters"


@router.get("/login")
def login(request: Request, error: str | None = None, next: str | None = None):
    if request.session.get("user"):
        return RedirectResponse(url="/characters", status_code=303)
    return templates.TemplateResponse(
        "auth/login.html",
        {
            "request": request,
            "providers": configured_providers(),
            "error": error,
            "next": _safe_next(request, next),
        },
    )


@router.get("/auth/{provider}/login")
async def auth_login(provider: str, request: Request, next: str | None = None):
    _provider_or_404(provider)
    # Remember where to land after the round trip.
    request.session["post_login_next"] = _safe_next(request, next)
    client = oauth.create_client(provider)
    if APP_BASE_URL:
        redirect_uri = f"{APP_BASE_URL}/auth/{provider}/callback"
    else:
        redirect_uri = str(request.url_for("auth_callback", provider=provider))
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/auth/{provider}/callback", name="auth_callback")
async def auth_callback(provider: str, request: Request, db: Session = Depends(get_db)):
    _provider_or_404(provider)
    client = oauth.create_client(provider)
    try:
        token = await client.authorize_access_token(request)
    except Exception:
        return RedirectResponse(url="/login?" + urlencode({"error": "Sign-in failed. Please try again."}), status_code=303)

    claims = token.get("userinfo") or {}
    email = resolve_email(claims)
    # Authelia sets email_verified; Entra omits it entirely (None), which is
    # fine — we only reject an *explicit* false.
    if not email or claims.get("email_verified") is False:
        return RedirectResponse(
            url="/login?" + urlencode({"error": "Your account did not return a usable email address."}),
            status_code=303,
        )

    user = resolve_login_user(
        db,
        email=email,
        display_name=claims.get("name") or claims.get("preferred_username") or email,
        auth_source=provider,
    )
    if user is None:
        return RedirectResponse(
            url="/login?" + urlencode({"error": "Your account isn't registered for this table. Ask an admin to add you."}),
            status_code=303,
        )
    if user.is_disabled:
        # Distinct from "not registered" — different situations for an
        # admin troubleshooting someone's access.
        return RedirectResponse(
            url="/login?" + urlencode({"error": "This account has been disabled. Contact an admin."}),
            status_code=303,
        )

    request.session["user"] = session_payload(user)
    target = request.session.pop("post_login_next", "/characters")
    return RedirectResponse(url=target, status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
