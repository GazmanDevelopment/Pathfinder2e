# Character Sheet App — Project Guide

A self-hosted web app that mirrors a Pathfinder 2e character sheet, for a small
home game. Runs as a container on TrueNAS SCALE behind an existing reverse proxy.

**This game is not strict rules-as-written.** The DM grants bonuses and items
that don't exist in the rulebook. The single most important design principle
follows from that: this is a **free-form editable sheet, not a rules engine.**
Never build validation that "corrects" a value or rejects content that isn't in
a canonical list. Every field is directly editable; anything the user invents is
a first-class citizen.

---

## Core design decisions (read before coding)

1. **Free-form, not a rules engine.** No derived-only values that lock out a
   manual override; no fixed catalogue a row must conform to. A homebrew item or
   a GM-granted spell is just another row the user types.

2. **Auth: the app is an OpenID Connect (OIDC) client trusting *two* providers.**
   The requirement is "Azure SSO **and** local username/password/OTP." Authelia
   authenticates only against its own file/LDAP backend — it *cannot* delegate to
   Entra (open feature request, not shipped). So:
   - **Microsoft Entra ID** — direct OIDC, for the SSO users.
   - **Authelia** — owns local usernames, Argon2 password hashes, and TOTP;
     exposed to the app as a standard OIDC provider.
   - The app shows two "Sign in with…" buttons, both authorization-code flows.
     Each returns a verified `email` claim; the app maps that to its own `users`
     row and sets its own session cookie.
   - **Do NOT hand-roll** password hashing, resets, lockouts, or TOTP — those
     live in Authelia. Do NOT use Authelia forward-auth as the only gate; it
     can't offer the second (Entra) door.
   - Access is gated by an **allow-list**: a login only gets characters if its
     email is pre-registered in `users`. First login becomes admin. Every query
     is scoped by `user_id`.

3. **Reference lookup is "copy, don't link".** (Phase 7.) A read-only
   `reference_library` seeded from an open PF2e dataset is a *prefill source*,
   never a constraint. Selecting an entry **copies** its fields into an editable
   character row; from then the row is independent and fully overridable. An
   optional nullable `reference_id` + `reference_version` on the row buys a
   "differs from book" marker and an opt-in per-row re-sync — the character copy
   always wins.

4. **SQLite is the datastore.** 4–5 concurrent users is trivial for it.
   **Enable WAL mode** (readers never block the writer). Keep a clean data-access
   layer so switching to Postgres later is a connection-string change, not a
   rewrite. Only reason to move: multiple app instances across machines, or
   sustained heavy writes — neither applies here.

---

## Tech stack

Default recommendation (pick the ecosystem you'll maintain):

- **Python — FastAPI + Jinja templates + HTMX.** Small, one process. `authlib`
  handles both OIDC providers; HTMX gives inline add/edit/remove on the spell and
  equipment lists without a SPA. **Preferred.**
- **Alternative — Next.js + Auth.js.** Auth.js has first-class multi-provider
  OIDC (Entra + a generic OIDC provider for Authelia) nearly as config.

Images: **Pillow** for avatar resize/thumbnail. Dice: **plain JS**, no library
needed (a tiny dice-notation parser is enough).

---

## Data model

`users 1—∞ characters`, and `characters 1—∞` each child table. A character page
is one query per section; an edit is a single insert/update/delete.

| Table | Holds | Sheet section |
|---|---|---|
| `users` | email, display name, role, auth source | login |
| `characters` | name, ancestry, class, level, size, speed, languages, alignment, HP cur/max, AC, class DC, spell DC/atk, perception, hero points, `avatar_path` | Header + Core Statistics |
| `ability_scores` | str/dex/con/int/wis/cha score + mod (or 6 columns on `characters`) | Ability Scores |
| `proficiencies` | saves & skills: name, `bonus`, rank (Trained…Legendary) | Saving Throws & Skills |
| `spells` | name, rank, uses/slots, action cost, range, effect, flags, `attack_bonus`, `damage_formula` | Spells |
| `equipment` | item, bonus/damage text, notes, qty, container, `attack_bonus`, `damage_formula`, `agile` flag | Equipment |
| `features` | source (ancestry/order/class/feat), name, effect, level gained | Ancestry/Order/Class Features |
| `notes` | free text: level-up summary, combat reference, GM notes | Changes / Quick Reference |
| `reference_library` | canonical spells/items/feats imported from open dataset; type, name, structured fields, source, version (Phase 7) | lookup source |

`characters` also carries `parent_character_id` (nullable, self-referential) and
`is_archived` (Phase 8) — see below.

### Avatars
Store the **image file on the dataset** (`uploads/` dir); keep only `avatar_path`
in the DB — no blobs in SQLite. On upload: validate MIME + cap size, resize to a
max and generate a thumbnail (Pillow), strip EXIF, save under a random filename.
Serve through the app, scoped to logged-in users, with a placeholder when unset.

### Tap to roll — anything with a modifier
Pure client-side JS. Two tiers:
- **Saves, skills, ability checks — no new fields.** Just `d20 + modifier`, and
  the modifier is already stored (`proficiencies.bonus`, the ability mods). Tap
  → roll, show the die + total, highlight natural 20 / natural 1.
- **Weapons & spells — needs the parseable fields.** `attack_bonus` (`+4`) and
  `damage_formula` (`2d4+2` dice notation) on the row. Tap → attack roll, then
  damage with a visible breakdown. A row with no formula shows no roll button.
- Optional: **crit** action that doubles the damage total (PF2e). **MAP** toggle
  −5/−10 (−4/−8 with `agile`) for follow-up attacks.

It's a **convenience roller, not a rules engine**: it rolls dice and does
arithmetic; it doesn't know enemy AC, doesn't auto-decide crits, doesn't track
conditions. A shared roll log (rolls on everyone's screen) is a bigger,
separate build needing live connections — out of scope for now.

---

## Deployment — TrueNAS SCALE

Recent SCALE releases run apps as **Docker Compose** (Kubernetes engine dropped
since Electric Eel). Deploy via the Apps UI **Custom App (YAML)** or as a compose
stack alongside your other services.

- **Persistence:** SQLite file + Authelia config + `uploads/` all on a dataset —
  snapshot it and backups/rollback come free from ZFS.
- **Secrets:** Entra client secret, Authelia JWT/session/storage keys, and (Phase
  8, optional) an Anthropic API key → env or a secrets file on the dataset.
  Never in the image.
- **TLS & routing:** the existing reverse proxy (Traefik/Caddy) + Let's Encrypt.
  Two routes: the app and Authelia.

```yaml
# sketch — app + local-auth, persisted to a dataset
services:
  sheet:
    image: ghcr.io/you/sheet-app:latest
    environment:
      OIDC_ENTRA_ISSUER: "https://login.microsoftonline.com/<tenant>/v2.0"
      OIDC_AUTHELIA_ISSUER: "https://auth.example.com"
      DATABASE_URL: "sqlite:////data/sheet.db"
    volumes:
      - /mnt/pool/apps/sheet:/data        # DB + uploads live on a dataset
  authelia:
    image: authelia/authelia:latest
    volumes:
      - /mnt/pool/apps/authelia:/config   # users_database.yml, secrets
# Traefik/Caddy already fronts both with Let's Encrypt
```

---

## Build order

Each phase leaves something that runs. **Auth comes after the app works.**

- **Phase 0 — Scaffold.** Empty app, Dockerfile, one page renders, compose
  deploys to TrueNAS. Prove the pipeline first.
- **Phase 1 — The sheet, single-user, no login.** Data model + full CRUD. Create
  a character; add/edit/remove spells and equipment; edit scalar fields; upload
  an avatar. Enter a real character and get it correct. **This is the product —
  nail it first.**
- **Phase 2 — Local login via Authelia.** Authelia with a file backend, enrol a
  user, add TOTP. Wire the app as its OIDC client. Real login + session.
- **Phase 3 — Add the Entra path.** Register the app in Entra, add "Sign in with
  Microsoft" as the second provider. Both paths resolve to the same `users` row
  by email.
- **Phase 4 — Multi-user & scoping.** Scope every query by `user_id`, add the
  allow-list, make one account admin. Each player sees only their own characters.
- **Phase 5 — Polish & safety net.** Print/export view, dataset snapshot schedule
  for backups, optional Pathbuilder JSON import.
- **Phase 6 — Tap to roll.** The dice roller: saves/skills/abilities from stored
  modifiers (no new fields); `attack_bonus`/`damage_formula` on weapons & spells;
  nat-20/nat-1 highlight; optional crit + MAP.
- **Phase 7 — Rules lookup & reference library.** Ingest the open PF2e dataset
  into `reference_library`; search box on add-spell/add-item prefills a row (roll
  fields included), fully editable after; override marker; save-homebrew-back.
  Purely additive — the sheet works without it.
- **Phase 8 — AI-assisted level-up (optional).** Send the current sheet + a
  free-text note to Claude, get back a proposed leveled-up sheet, review it as a
  diff, archive the pre-level-up character on accept. Fully optional; the app is
  complete and usable without it. See below.

---

## Data source & licensing (Phase 7)

Cleanest structured source: the community-maintained **Foundry VTT `pf2e`
system** (`github.com/foundryvtt/pf2e`) — JSON packs for every spell, item, feat.
PF2e mechanics are broadly open (OGL/SRD, and Paizo's ORC licence for the
Remaster). A **private, non-commercial, self-hosted** sheet for one table sits
inside Paizo's Community Use Policy: keep it unmonetised, attribute Paizo, don't
wholesale-republish flavour text or trademarks. Import an openly-licensed
structured dataset rather than scraping Archives of Nethys' presentation.

---

## AI-assisted level-up (optional, Phase 8)

Build this last, after everything else works. It's an optional convenience, not
a dependency of the sheet — if the API key isn't configured, the feature is
simply hidden and the app behaves exactly as it does today.

**What it does:** the player types a free-text note ("hit level 5, took Toughness,
DM gave me a +1 striking dagger and let me swap a skill feat") on their character
page. The app serializes the character's *entire* current sheet (all child
tables) plus that note into a prompt, sends it to the Claude API, and gets back
a proposed *complete* leveled-up sheet — new level, HP, proficiency ranks, new
spell slots, suggested new spells/feats/features, updated DCs — as structured
data matching the existing data model.

**Never write directly.** This follows the same principle as reference lookup
(§3 above): the model's output is a *prefill*, not an authority.
- Show the proposal as a **diff view** against the current sheet: every changed
  field and every added/removed row, old vs. proposed, before/after.
- The user accepts, edits, or rejects **per field/per row** — nothing is applied
  wholesale. A proposed value the DM wouldn't allow just gets edited or dropped,
  same as typing over any other field on this sheet.
- Claude is working from published PF2e leveling rules and can be wrong or
  miss table-specific homebrew; the note is the user's way to steer it, not a
  guarantee it will be followed correctly. Treat its output with the same
  skepticism as a first-pass suggestion, never as ground truth.

**Archive on accept.** When the user accepts the leveled-up sheet:
- Clone the current character row (and its child rows) into a new row with
  `is_archived = true` and `parent_character_id` pointing at... itself being the
  prior state — i.e. the *archive* keeps the old level's data, and the live
  character row is updated in place to the new level, with the archive's
  `parent_character_id` set to the live character's id. (Equivalently: archives
  form a backward-linked history chain off the live character.)
- Archived characters are **read-only** and shown as a "history" list under the
  live character (e.g. "Level 4 (archived 2026-08-29)"), not in the main
  character picker.
- This reuses ordinary character CRUD — an archive is just another `characters`
  row — no new table needed.

**Implementation notes:**
- New secret: an Anthropic API key. Same handling as the Entra client secret —
  env var or secrets file on the dataset, never in the image (see Deployment).
  If unset, hide the "Level up with AI" entry point entirely.
- The prompt should include the full sheet (abilities, proficiencies, spells,
  equipment, features, notes) so Claude has real context, not just the level
  number — this is a homebrew-heavy game and the note alone won't carry enough
  information about what the character already has.
- No retries/auto-apply loop, no background job — this is a synchronous
  request-and-review action the player does once per level-up.

---

## Reality check

Wanderer's Guide, Pathbuilder, and Foundry VTT already do PF2e character
management for free. The reason to build this anyway: the game is homebrew-heavy,
and those tools are built around rules-as-written — they get awkward the moment
the DM grants something off-book. A sheet that never second-guesses what you type,
on your own hardware, is the point. The trade: you give up their automation and
maintained spell/item libraries. Worth it for a homebrew table.