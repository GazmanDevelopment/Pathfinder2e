from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import configured_providers
from app.db import get_db
from app.models import User

# authlib's Starlette integration. Each configured provider is registered by
# name; the same names appear in /auth/{provider}/... URLs.
oauth = OAuth()

for _provider in configured_providers():
    _register_kwargs = {
        "name": _provider["name"],
        "client_id": _provider["client_id"],
        "client_secret": _provider["client_secret"],
        "server_metadata_url": _provider["server_metadata_url"],
        "client_kwargs": {"scope": "openid email profile"},
    }
    if _provider["name"] == "authelia":
        # Authelia's client registration only allows client_secret_post
        # (authelia/configuration.yml's token_endpoint_auth_method); authlib
        # defaults to client_secret_basic, which Authelia's token endpoint
        # rejects with invalid_client. Entra accepts either, so it's left
        # off entirely for other providers rather than passed as None —
        # authlib doesn't treat an explicit None the same as an absent key.
        _register_kwargs["token_endpoint_auth_method"] = "client_secret_post"
    oauth.register(**_register_kwargs)


class NotAuthenticated(Exception):
    """Raised by require_login when the session has no user.

    Turned into a redirect (browser), an HX-Redirect (htmx), or a 401 (API)
    by the handler registered in main.py.
    """


class AccountDisabled(Exception):
    """Raised by require_login when the session's user has since been
    disabled (or deleted). Distinct from NotAuthenticated so the redirect can
    carry its own message instead of the generic "please sign in" one.
    """


def require_login(request: Request, db: Session = Depends(get_db)) -> dict:
    """Dependency guarding every sheet route. Returns the session user dict.

    Re-checks the account is still active on every request, not just at
    login time — Phase 4b's disable has to take effect immediately, even for
    a session that's already open, so the cached session identity alone
    isn't enough here (unlike role, which Phase 4 does trust from the
    session — disable is the one case that must not wait for next login).
    """
    user = request.session.get("user")
    if not user:
        raise NotAuthenticated()

    db_user = db.get(User, user["id"])
    if db_user is None or db_user.is_disabled:
        request.session.clear()
        raise AccountDisabled()

    return user


def require_admin(request: Request, db: Session = Depends(get_db)) -> dict:
    """Dependency guarding /admin/*. No session -> same redirect-to-login as
    require_login; a logged-in non-admin gets a plain 403 (already rendered
    as a themed page by the global HTTPException handler)."""
    user = require_login(request, db)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admins only.")
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


def resolve_login_user(db: Session, *, email: str, display_name: str | None, auth_source: str) -> User | None:
    """Resolve a successful OIDC login to a users row, enforcing the allow-list.

    Every provider callback funnels through here, so the Entra and Authelia
    paths converge on one users row per email. `email` is expected already
    normalised (see resolve_email).

    Access is gated: only a *pre-registered* email (a row already present in
    `users`, added via /admin/users) can log in — this function never
    auto-creates a row for an unrecognised email. The one exception is the
    very first login ever, which bootstraps the admin account, since
    otherwise nobody could ever add themselves to an empty allow-list.

    Returns None when the email isn't registered — the caller must treat
    that as a rejected login, not create a session.
    """
    if db.query(User).count() == 0:
        user = User(email=email, display_name=display_name, auth_source=auth_source, role="admin")
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    user = db.query(User).filter(User.email == email).one_or_none()
    if user is None:
        return None

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
