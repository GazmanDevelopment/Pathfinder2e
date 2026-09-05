import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{(REPO_ROOT / 'data' / 'sheet.db').as_posix()}")
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(REPO_ROOT / "uploads")))

MAX_AVATAR_BYTES = 8 * 1024 * 1024
AVATAR_MAX_DIM = 512
AVATAR_THUMB_DIM = 128

# --- Auth (Phase 2) -------------------------------------------------------

# Signing key for the session cookie. Required — auth is always on, so there
# is no safe default. Missing it is a hard startup failure (see main.py).
SESSION_SECRET = os.environ.get("SESSION_SECRET", "")

# Public base URL, used to build an absolute redirect_uri behind the reverse
# proxy (which otherwise hides the real scheme/host). Falls back to
# request.url_for when unset.
APP_BASE_URL = os.environ.get("APP_BASE_URL", "").rstrip("/")

# Send the session cookie only over HTTPS. True in production behind TLS;
# set SESSION_HTTPS_ONLY=false for plain-http local dev.
SESSION_HTTPS_ONLY = os.environ.get("SESSION_HTTPS_ONLY", "true").lower() != "false"

# Path to Authelia's users_database.yml, mounted read-write into this
# container (Phase 4c). Unset hides the "Set password" admin action
# entirely, same pattern as an unconfigured OIDC provider hiding its
# sign-in button — there's no real dev/local equivalent since it means
# writing into a real Authelia file backend.
AUTHELIA_USERS_DB_PATH = os.environ.get("AUTHELIA_USERS_DB_PATH", "")

# AI level-up (Phase 8, optional). Two interchangeable backends — which one
# is actually used is a single deployment-wide choice, not a per-request
# toggle: an admin/deployer picks the provider that suits their setup.
#
# "anthropic" (the default): Claude API, via ANTHROPIC_API_KEY.
# "ollama": a self-hosted Ollama or Open WebUI server's OpenAI-compatible
# chat-completions endpoint, via OLLAMA_BASE_URL/OLLAMA_MODEL. Structured
# JSON output is meaningfully less reliable on local open-weight models
# than Claude's schema-guaranteed structured outputs — app/ai_levelup.py's
# Ollama path retries once with a sharper instruction before giving up.
AI_LEVELUP_PROVIDER = os.environ.get("AI_LEVELUP_PROVIDER", "anthropic").strip().lower()

# Same silently-hidden-optional pattern as AUTHELIA_USERS_DB_PATH above, not
# SESSION_SECRET's hard startup failure — unset simply hides the "Level up
# with AI" entry point and the admin grant/revoke toggle, no code path
# depends on either existing. See ai_levelup_configured() below, which
# checks only whichever provider is actually active.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# e.g. "http://192.168.1.50:11434/v1" for a raw Ollama server, or whatever
# OpenAI-compatible base URL an Open WebUI instance exposes — this app
# appends "/chat/completions" itself, so include no trailing path here.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")


def ai_levelup_configured() -> bool:
    """Whether the currently-active provider has everything it needs —
    gates the "Level up with AI" entry point and the admin toggle. Checks
    only the active provider's own requirements, even if the other
    provider's env vars also happen to be set."""
    if AI_LEVELUP_PROVIDER == "ollama":
        return bool(OLLAMA_BASE_URL and OLLAMA_MODEL)
    return bool(ANTHROPIC_API_KEY)

# OIDC providers, keyed by the name used in /auth/{provider}/... URLs. Entra
# joins this dict in Phase 3; the login page and routes are already generic
# over it. `label` is what the "Sign in with …" button shows.
_PROVIDER_LABELS = {
    "authelia": "local account",
    "entra": "Microsoft",
}


def _provider_config(name: str) -> dict | None:
    prefix = f"OIDC_{name.upper()}_"
    issuer = os.environ.get(f"{prefix}ISSUER", "").rstrip("/")
    client_id = os.environ.get(f"{prefix}CLIENT_ID", "")
    client_secret = os.environ.get(f"{prefix}CLIENT_SECRET", "")
    if not (issuer and client_id and client_secret):
        return None
    return {
        "name": name,
        "label": _PROVIDER_LABELS.get(name, name),
        "issuer": issuer,
        "client_id": client_id,
        "client_secret": client_secret,
        "server_metadata_url": f"{issuer}/.well-known/openid-configuration",
    }


def configured_providers() -> list[dict]:
    """Providers with all three env vars present, in a stable order."""
    return [cfg for name in _PROVIDER_LABELS if (cfg := _provider_config(name))]
