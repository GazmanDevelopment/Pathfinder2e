"""A minimal OIDC provider for LOCAL DEV AND TESTING ONLY.

It exists because Authelia runs as a container and can't be stood up on a
machine without Docker. It speaks just enough OpenID Connect for authlib to
complete a real authorization-code flow — discovery, authorize, token, JWKS,
userinfo — so the app's OIDC *client* can be exercised end to end.

It is NOT a real identity provider: /authorize performs no authentication and
issues a code for whatever email it's given. Never run it anywhere real, and
never import it from app.main.

Run it:  uvicorn devtools.mock_oidc:app --port 9000
Point the app's provider env at it:
  OIDC_AUTHELIA_ISSUER=http://localhost:9000
  OIDC_AUTHELIA_CLIENT_ID=devclient
  OIDC_AUTHELIA_CLIENT_SECRET=devsecret
"""
import time
import uuid
from urllib.parse import urlencode

from authlib.jose import JsonWebKey, jwt
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

ISSUER = "http://localhost:9000"
CLIENT_ID = "devclient"
CLIENT_SECRET = "devsecret"
DEFAULT_EMAIL = "dev@example.com"
DEFAULT_NAME = "Dev User"

# One RSA keypair for the process lifetime; the public half is served at /jwks
# so authlib can verify the id_token signature.
_key = JsonWebKey.generate_key("RSA", 2048, is_private=True)
_KID = "mock-key-1"
_PUBLIC_JWK = _key.as_dict(is_private=False, kid=_KID)
_PRIVATE_PEM = _key.as_pem(is_private=True)

app = FastAPI(title="Mock OIDC Provider")

# code -> claims, consumed once at the token endpoint.
_codes: dict[str, dict] = {}


@app.get("/.well-known/openid-configuration")
def discovery():
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
        "userinfo_endpoint": f"{ISSUER}/userinfo",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "email", "profile"],
    }


@app.get("/jwks")
def jwks():
    return {"keys": [_PUBLIC_JWK]}


@app.get("/authorize")
def authorize(request: Request):
    """No real login. Auto-issue for the default email, or show a tiny form so
    a test can pick the email (drives the re-login / multi-user cases)."""
    params = dict(request.query_params)
    email = params.get("login_hint")
    if email:
        return _issue_code(params, email, DEFAULT_NAME)

    # Minimal chooser so a human or Playwright can pick an identity.
    fields = "".join(
        f'<input type="hidden" name="{k}" value="{v}">' for k, v in params.items()
    )
    return HTMLResponse(
        f"""<!doctype html><title>Mock sign-in</title>
        <form method="post" action="/authorize">
          {fields}
          <label>Email <input name="email" value="{DEFAULT_EMAIL}"></label>
          <label>Name <input name="name" value="{DEFAULT_NAME}"></label>
          <button type="submit" id="mock-approve">Approve</button>
        </form>"""
    )


@app.post("/authorize")
async def authorize_submit(request: Request):
    # The OAuth params rode along as hidden inputs, so the whole form body
    # carries redirect_uri/state/nonce plus the chosen email/name.
    form = dict(await request.form())
    email = form.pop("email", DEFAULT_EMAIL)
    name = form.pop("name", DEFAULT_NAME)
    return _issue_code(form, email, name)


def _issue_code(params: dict, email: str, name: str):
    code = uuid.uuid4().hex
    _codes[code] = {"email": email, "name": name, "nonce": params.get("nonce")}
    query = {"code": code}
    if params.get("state"):
        query["state"] = params["state"]
    return RedirectResponse(url=f"{params['redirect_uri']}?{urlencode(query)}", status_code=302)


@app.post("/token")
async def token(request: Request):
    form = await request.form()
    code = form.get("code")
    data = _codes.pop(code, None)
    if data is None:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "sub": data["email"],
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + 3600,
        "email": data["email"],
        "email_verified": True,
        "name": data["name"],
    }
    if data.get("nonce"):
        payload["nonce"] = data["nonce"]

    header = {"alg": "RS256", "kid": _KID}
    id_token = jwt.encode(header, payload, _PRIVATE_PEM).decode("ascii")
    return JSONResponse(
        {
            "access_token": uuid.uuid4().hex,
            "token_type": "Bearer",
            "expires_in": 3600,
            "id_token": id_token,
        }
    )


@app.get("/userinfo")
def userinfo():
    return {"sub": DEFAULT_EMAIL, "email": DEFAULT_EMAIL, "email_verified": True, "name": DEFAULT_NAME}
