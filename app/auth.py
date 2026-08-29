from authlib.integrations.starlette_client import OAuth
from fastapi import Request
from sqlalchemy.orm import Session

from app.config import configured_providers
from app.models import User

# authlib's Starlette integration. Each configured provider is registered by
# name; the same names appear in /auth/{provider}/... URLs.
oauth = OAuth()

for _provider in configured_providers():
    oauth.register(
        name=_provider["name"],
        client_id=_provider["client_id"],
        client_secret=_provider["client_secret"],
        server_metadata_url=_provider["server_metadata_url"],
        client_kwargs={"scope": "openid email profile"},
    )


class NotAuthenticated(Exception):
    """Raised by require_login when the session has no user.

    Turned into a redirect (browser), an HX-Redirect (htmx), or a 401 (API)
    by the handler registered in main.py.
    """


def require_login(request: Request) -> dict:
    """Dependency guarding every sheet route. Returns the session user dict."""
    user = request.session.get("user")
    if not user:
        raise NotAuthenticated()
    return user


def resolve_email(claims: dict) -> str | None:
    """Pull a login email out of an id_token's claims, normalised to lowercase.

    Authelia always sends `email`. Microsoft Entra v2.0 often does *not* — it
    puts the address in `preferred_username` (the UPN), and only emits `email`
    when the tenant exposes it as an optional claim. Fall back through the
    usual Entra locations so both providers land on the same address, and
    lowercase it so `Gareth@x` and `gareth@x` map to one users row.
    """
    for key in ("email", "preferred_username", "upn"):
        value = claims.get(key)
        if value and "@" in value:
            return value.strip().lower()
    return None


def upsert_user(db: Session, *, email: str, display_name: str | None, auth_source: str) -> User:
    """Find the user by verified email, or create them.

    Every provider callback funnels through here, so the Entra and Authelia
    paths converge on one users row per email. `email` is expected already
    normalised (see resolve_email).
    """
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        user = User(email=email, display_name=display_name, auth_source=auth_source)
        db.add(user)
    else:
        # Keep the display name and most-recent source fresh, but never
        # overwrite the row's identity (email) or role.
        if display_name:
            user.display_name = display_name
        user.auth_source = auth_source
    db.commit()
    db.refresh(user)
    return user


def session_payload(user: User) -> dict:
    """The minimal identity stored in the signed session cookie."""
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
    }
