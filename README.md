# Character Sheet

A self-hosted Pathfinder 2e character sheet for a small home game — one
process, one SQLite file, running as a container on a home NAS behind a
reverse proxy.

## Why this exists

Wanderer's Guide, Pathbuilder, and Foundry VTT already do PF2e character
management for free, and do it well. The reason to build this anyway: this
table's game is homebrew-heavy, and those tools are built around
rules-as-written — they get awkward the moment the GM grants something
off-book. This sheet never second-guesses what you type. Every field is
directly editable; a GM-granted item, a homebrew feat, an off-book bonus is
just another row, not something the app tries to validate against a
canonical list. It's a record of what happened at the table, not a rules
engine.

## What's here

- **The sheet itself** — header, core stats, ability scores, saves &
  skills, spells, equipment, features, and notes, each independently
  editable inline (HTMX, no page reloads). Every collapsible section, tap
  targets sized for a phone at the table, a print/PDF-export view.
- **Auth** — two "Sign in with…" buttons: a local account (Authelia, with
  TOTP) and Microsoft Entra SSO, both resolving to the same allow-listed
  user by verified email. An admin role manages the player allow-list, can
  disable or delete accounts, and can set a local player's password
  directly from the admin page instead of hand-editing Authelia's config
  over SSH.
- **Multi-user scoping** — each player sees only their own characters; an
  admin can see and edit anyone's (GM support).
- **Pathbuilder import/export** — pull a character straight from a
  Pathbuilder 2e build ID, or export one of your own characters back to a
  Pathbuilder-shaped JSON file. Both directions are a best-effort prefill,
  not an authority — nothing here computes a derived total (a skill bonus,
  a DC) it isn't confident about; those are left blank for you to fill in
  rather than risk a confidently wrong number.
- **Backups** — the SQLite DB, uploaded avatars, and Authelia's own config
  all live on one dataset, covered by a single periodic snapshot schedule.

See [CLAUDE.md](CLAUDE.md) for the full design rationale, data model, and
build order (what's shipped and what's still ahead — tap-to-roll, a PF2e
reference-library lookup, and an optional AI-assisted level-up flow).

## Tech stack

Python, FastAPI, Jinja2 templates, HTMX for inline editing, SQLAlchemy over
a WAL-mode SQLite file, Pillow for avatar handling. No build step, no
frontend framework — server-rendered HTML with just enough JavaScript where
the browser needs a nudge (a print handler, a couple of small dropdowns).

## Running it locally

```bash
python -m venv .venv
.venv/Scripts/activate  # or `source .venv/bin/activate` on Linux/macOS
pip install -r requirements.txt

SESSION_SECRET=dev-only-secret uvicorn app.main:app --reload
```

Auth is always on (there's no "logged out" mode for the sheet itself), so
you'll need at least one OIDC provider configured — see
[docs/truenas-setup.md](docs/truenas-setup.md) for setting up Authelia
locally, or point `OIDC_ENTRA_*` at a real Entra app registration.

## Deploying

This runs as a Docker Compose stack (`docker-compose.yml`) — the app plus
an Authelia container for local-account login. The full walkthrough,
written for TrueNAS SCALE but applicable to any Docker host, is in
[docs/truenas-setup.md](docs/truenas-setup.md): secrets generation, the
reverse-proxy setup, email delivery for TOTP enrollment, and the
allow-list/admin workflow.
