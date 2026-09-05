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
| `users` | email, display name, role, auth source, `is_disabled` (Phase 4b) | login |
| `characters` | name, ancestry, class, level, size, speed, languages, alignment, HP cur/max, AC, class DC, spell DC/atk, perception, hero points, `avatar_path` | Header + Core Statistics |
| `ability_scores` | str/dex/con/int/wis/cha score + mod (or 6 columns on `characters`) | Ability Scores |
| `proficiencies` | saves & skills: name, `bonus`, rank (Trained…Legendary) | Saving Throws & Skills |
| `spells` | name, rank, uses/slots, action cost, range, effect (rich text, Phase 7), flags, `attack_bonus`, `damage_formula`, `reference_id`/`reference_version` (Phase 7) | Spells |
| `equipment` | item, bonus/damage text, notes (rich text, Phase 7), qty, container, `attack_bonus`, `damage_formula`, `agile` flag, `reference_id`/`reference_version` (Phase 7) | Equipment |
| `features` | source (ancestry/order/class/feat), name, effect (rich text, Phase 7), level gained, `reference_id`/`reference_version` (Phase 7) | Ancestry/Order/Class Features |
| `notes` | rich text (issue #29): level-up summary, combat reference, GM notes | Changes / Quick Reference |
| `reference_library` | canonical spells/equipment/feats ingested from the Foundry VTT `pf2e` system (Phase 7 — done): `entry_type`, `foundry_id`, name, source/`source_version`, rank, action cost, range, uses, effect (rich text), damage formula, agile, level gained, `license`/`publication_title` | lookup source, prefills Spells/Equipment/Features |

`characters` also carries `parent_character_id` (nullable, self-referential) and
`is_archived` (Phase 8) — see below.

### Avatars
Store the **image file on the dataset** (`uploads/` dir); keep only `avatar_path`
in the DB — no blobs in SQLite. On upload: validate MIME + cap size, resize to a
max and generate a thumbnail (Pillow), strip EXIF, save under a random filename.
Serve through the app, scoped to logged-in users, with a placeholder when unset.

### Tap to roll — anything with a modifier (Phase 6 — done)
Pure client-side JS (`app/static/dice.js`), no server changes at all — no new
routes, no new DB columns. A single `document`-level delegated click listener
(not per-row bindings) reads `data-roll`/`data-roll-mod`/`data-roll-formula`/
`data-roll-label`/`data-roll-agile` off whichever trigger was tapped; this
survives htmx's `outerHTML` row swaps with no rebinding, unlike this app's
usual per-partial inline-script convention, which a listener bound directly
to a row element would not.
- **Saves, skills, ability checks — no new fields.** Just `d20 + modifier`,
  and the modifier is already stored (`Proficiency.bonus`, the ability
  `*_mod` columns). Tap → roll, show the die + total, highlight natural 20 /
  natural 1 (gold/crimson glow on that one d20 only — never a damage die,
  regardless of its value). A proficiency with no bonus entered yet still
  rolls, as `+0` (never written back to the DB, purely a transient default
  for the roll action) — an unset ability score has no such default and
  shows no roll button at all, since "not entered yet" isn't the same as
  "trained at +0."
- **Weapons & spells — needs the parseable fields.** `attack_bonus` and
  `damage_formula` (`2d4+2` dice notation, parsed by `dice.js`'s
  `parseFormula()`) on the row, independently tappable — Atk and Dmg are two
  separate triggers, never auto-chained, since confirming a hit is the DM's
  call, not this tool's. A row with no formula/bonus shows no roll button.
- **Crit** doubles a settled damage total in place (arithmetic only, never
  re-rolls the dice); toggleable, reversible without closing the tray.
  **MAP** buttons (`+0`/`−5`/`−10`, or `−4`/`−8` with `agile`) appear only on
  an attack roll and recompute its total in place the same way — neither
  persists past that one open roll.
- **Manual entry**: a "Roll dice" button in the sheet toolbar opens the same
  overlay with a plain formula box (e.g. typing `2d6+4`); unparseable input
  shows a friendly inline error rather than crashing.
- The roll shows as a small modal-like tray (`app/templates/characters/
  _dice_tray.html`) with each die visually flying in (CSS `@keyframes
  die-fly`: translate + rotate + scale, staggered per die so a multi-die
  damage roll cascades in) before settling on its face — this app's first
  CSS animation and first overlay/modal UI, so it reuses the existing
  parchment/gold palette and shape tokens rather than introducing new ones.

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
- **Secrets:** Entra client secret, Authelia JWT/session/storage keys, an SMTP
  credential for Authelia's notifier (TOTP/WebAuthn/reset emails need real
  delivery — the filesystem notifier only writes to a local file no player
  can see), and (Phase 8, optional) an Anthropic API key → env or a secrets
  file on the dataset. Never in the image.
- **`authelia/configuration.yml` and `authelia/users_database.yml` are
  git-tracked templates, but the deployed box's real copies have actual
  secrets, the real domain, and the RSA issuer key hand-edited in.** The box
  has both marked `git update-index --skip-worktree` so a `git pull` there
  never touches them or risks staging real secrets into a commit. **Whenever
  a change here touches either file, say so explicitly** — the user's next
  `git pull` on the box won't apply it automatically; they need to
  temporarily `git update-index --no-skip-worktree`, diff, and manually
  reapply anything relevant. See docs/truenas-setup.md for the full
  workflow.
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
- **Phase 4b — Disable or delete users.** A separate view or filter on
  `/admin/users` for disabled accounts, alongside the active list. See below.
- **Phase 4c — Admin sets a local account's password.** Lets an admin type a
  starting password for a player from `/admin/users` instead of hand-editing
  Authelia's `users_database.yml` over SSH. See below.
- **Phase 5 — Polish & safety net.** Print/export view — **done**: a Print
  button on the character sheet triggers the browser's own print dialog
  (handles PDF export for free); print-specific CSS hides interactive chrome
  and forces every collapsible section open regardless of its on-screen
  state, since a collapsed `<details>` can't be forced open with CSS alone
  in current browsers — verified directly, see `app/templates/characters/
  sheet.html`'s `beforeprint`/`afterprint` handlers. No new route or
  duplicate template; the existing sheet page adapts in place. Dataset
  snapshot schedule for backups — **done**: a TrueNAS Periodic Snapshot
  Task on the `pf2e-sheets` dataset (recursive, daily, two-week retention)
  covers the SQLite DB, uploads, and Authelia's config/secrets together in
  one snapshot — pure TrueNAS UI configuration, no code; see
  `docs/truenas-setup.md` §11 for the exact steps and what a same-pool
  snapshot does and doesn't protect against. Optional Pathbuilder JSON
  import — **done**: an "Import from Pathbuilder" form on the character
  list takes a Pathbuilder build ID, fetches the real export (confirmed
  live against `pathbuilder2e.com/json.php?id=...`, not guessed), and maps
  it into a new character. Never computes a derived total (skill/save
  bonus, DC, damage formula) it isn't fully confident about — those are
  left blank rather than risk a confidently wrong number, matching this
  project's free-form-not-a-rules-engine stance; anything with no
  structured column (money, background, deity, casting/DC ranks) lands in
  one consolidated summary Note instead of being silently dropped. See
  `app/pathbuilder.py`. The reverse direction (exporting a character back
  to a Pathbuilder-shaped JSON file, GitHub issue #34) is also done,
  bundled in as the same field-mapping problem solved both ways — a plain
  download from the sheet page, explicitly one-way/lossy since this app
  tracks less structured detail than Pathbuilder does. Phase 5 is now
  fully done.
- **Phase 6 — Tap to roll — done.** The dice roller: saves/skills/abilities from
  stored modifiers (no new fields); `attack_bonus`/`damage_formula` on weapons &
  spells (independent Atk/Dmg triggers); nat-20/nat-1 highlight; Crit + MAP as
  transient per-roll toggles; a manual "2d6+4"-style entry box; each roll shown
  as an animated die flying in and settling, this app's first CSS animation and
  overlay UI. Pure client-side (`app/static/dice.js`) — no server changes. See
  below for the full detail.
- **Phase 7 — Rules lookup & reference library — done.** `reference_library`
  (14,147 rows: 1,994 spells, 5,869 equipment, 6,284 feats) is ingested from
  the Foundry VTT `pf2e` system's `packs/pf2e/{spells,equipment,feats}` JSON
  (not bestiaries/ancestries/classes/art — measured directly at ~31 MB, not
  the full ~2 GB repo) by `scripts/ingest_reference_library.py`, a one-off
  dev-machine script (sparse git clone, never run in Docker build or at
  container startup) that writes a vendored, committed snapshot to
  `app/data/reference_library.json`. The running app only ever reads that
  file (`app/db.py`'s `seed_reference_library()`, called at startup) — no
  network or git access at runtime. A search box on the add-spell/add-item/
  add-feature forms (`app/templates/_reference_results.html`, a hand-rolled
  live-search dropdown matching the proficiencies rank-field pattern, not a
  native `<datalist>`) prefills a still-unsaved row via each resource's
  `/new?reference_id=` route; nothing is ever auto-saved. `reference_id` +
  `reference_version` on `Spell`/`Equipment`/`Feature` give a "Book updated"
  marker and an opt-in "Refresh from library" re-fetch
  (`/{id}/edit?refresh_from_reference=1`) — the character's copy always
  wins until the user explicitly saves. Reference descriptions keep real
  HTML formatting (the `note_html` filter from issue #29 was generalized to
  `rich_text`/`clean_rich_text` in `app/templating.py`, now shared by Notes,
  Spells, Equipment, and Features alike) rather than being flattened to
  plain text. Attribution: each search result shows its source book/license
  (`ReferenceLibrary.publication_title`/`license`, carried straight through
  from Foundry's own `system.publication` fields), and a site-wide Paizo
  Community Use Policy / Foundry VTT credit notice
  (`app/templates/_attribution.html`) renders in `base.html`'s footer
  whenever the library has data — required by the OGL/ORC/Community Use
  licensing basis below, not just a README mention. Purely additive — with
  an empty/un-ingested library the search box just returns no results, and
  the attribution footer doesn't render at all.
- **Phase 8 — AI-assisted level-up (optional) — done.** Built and shipped
  across three stages/PRs (issue #55, GitHub PRs #56-#58); see below for the
  full detail on what was actually built, which differs from the original
  single-shot plan in a few real ways (a genuine multi-turn conversation, a
  switchable local/hosted backend, and additions-only proposals rather than
  wholesale rewrites of existing rows). Fully optional throughout — with no
  provider configured, the feature is invisible and the app behaves exactly
  as it does without it.

---

## Disable or delete users (Phase 4b)

Builds directly on Phase 4's allow-list and admin role. Two distinct admin
actions on `/admin/users`, not one:

- **Disable** — the normal path for a player who's left the table but has
  characters worth keeping. `users.is_disabled` (new column). A disabled
  account:
  - **Can't log in.** Rejected at the same point as an unregistered email,
    with its own message ("This account has been disabled — contact an
    admin"), not the "not registered" one — they're different situations for
    an admin troubleshooting access. Takes effect **immediately**, even for
    a session that's already open — the login-gate must re-check the
    database, not just trust the identity cached in the session cookie, the
    same principle Phase 4 already applied to character ownership.
  - **Vanishes from the admin's default "All Characters" view.** Their
    sheets aren't deleted or reassigned — they're filtered out of the normal
    admin list the same way a Phase 4 non-admin's view is filtered to their
    own characters.
  - **Is still reachable.** `/admin/users` gains a second view or filter —
    Active vs. Disabled — and clicking a disabled user there opens *their*
    character list (reusing the admin ownership-bypass from Phase 4, so
    opening one of their sheets to view or edit already works; the new part
    is just the filtered entry point to reach it). This is how an admin
    checks or archives what a departed player had before deciding whether to
    delete them outright.
- **Delete** — a hard removal of the `users` row itself, offered only for an
  account with **zero characters** (a mistyped or no-longer-needed
  allow-list entry, most often). An account that owns characters can only be
  disabled, never deleted, so deleting a user is never the thing that
  destroys someone's character data — that stays true to the project's
  free-form, never-destroy-data-casually stance. Deleting a user who still
  owns characters isn't offered by the UI; enforce it in the route too, not
  just by hiding the button.
- **Guard rail:** never allow the last remaining admin to be disabled or
  deleted, including by themselves. There's no supported way back in once
  `users` is non-empty (Phase 4's bootstrap only fires on an *empty* table),
  so a self-lockout there means direct database surgery to recover.

---

## Admin sets a local account's password (Phase 4c)

Today, adding a local (Authelia) player is a two-system, SSH-only chore: the
admin allow-lists their email in the app's `/admin/users` (Phase 4), *and*
separately runs `authelia crypto hash generate argon2` by hand and edits
`authelia/users_database.yml` on the box. Phase 4c collapses the second half
into the same admin page: a **"Set password"** action per local-account row
that takes a plaintext password and does the hashing and file write itself.

**Decided explicitly, not the alternative:** the admin types a password
directly, rather than the app triggering a self-service reset-email flow.
This was a deliberate choice over the "more hands-off" alternative — Authelia
has no documented admin-triggered-reset API to build that on anyway, and
direct admin-set passwords fit this app's actual scale (a GM setting up a
handful of known players) better than email-based self-service.

**This automates the documented manual process — it does not replace
Authelia as the source of truth for authentication:**
- The app computes an **Argon2id** hash matching Authelia's own default
  parameters (memory 65536 KiB, 3 iterations, parallelism 4, 16-byte salt,
  32-byte key) — the same hash Authelia's own CLI would produce, not a
  competing scheme. If a deployment ever customizes
  `authentication_backend.file.password.argon2` in `configuration.yml`, the
  app's parameters must be updated to match or the hash won't verify.
- The app writes/updates that user's entry directly in
  `authelia/users_database.yml` (creating the entry — username, display
  name, email, `disabled: false` — if the player doesn't have one yet, or
  just replacing the password hash if they do). `configuration.yml` needs
  `authentication_backend.file.watch: true` so Authelia live-reloads the
  file; without it this needs a manual Authelia restart to take effect.
- **What this still doesn't touch:** TOTP. Second-factor secrets live in
  Authelia's separate encrypted storage backend (its own database), not in
  `users_database.yml` — this feature can set a password but cannot read,
  set, or bypass anyone's TOTP enrollment. A player still enrols their own
  authenticator app through Authelia's normal flow on first login. Sessions
  and lockouts remain entirely Authelia's, unchanged from Phase 2 — this is
  the one narrow exception to "never hand-roll auth" in §2, and it's scoped
  as tightly as possible: password hashing only, using Authelia's own
  algorithm and parameters, writing to Authelia's own user store.

**Infrastructure this needs that doesn't exist yet:**
- The `sheet` container needs read-write access to
  `authelia/users_database.yml` — a new volume mount alongside the existing
  `data`/`uploads` ones, e.g. `./authelia:/authelia-config`, plus a
  `AUTHELIA_USERS_DB_PATH` env var pointing at the file. This is a real
  increase in blast radius if the app is ever compromised (write access to
  Authelia's credential store), worth weighing against the SSH-workflow
  convenience it buys.
- A Python Argon2 implementation (`argon2-cffi`) as a new dependency.

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

## AI-assisted level-up (Phase 8 — done)

Shipped across three stages/PRs against GitHub issue #55: foundation/permissions
(PR #56), the conversation engine + archive-on-accept (PR #57), and the
read-only archived view + History section + entry point (PR #58). Optional
throughout — with no provider configured, `ai_levelup_configured()`
(`app/config.py`) is false, the "Level up with AI" toolbar link never renders,
and every `/level-up` route 404s as if it doesn't exist.

**What it does — a real conversation, not one shot.** The player opens
"Level up with AI" from the sheet toolbar, types a free-text note ("hit level
5, took Toughness, DM gave me a +1 striking dagger"), and the app serializes
the character's entire current sheet plus the note into a prompt
(`app/ai_levelup.py`'s `character_to_dict()`/`build_system_blocks()`). The
model replies with a `message` (plain chat text) and, once it has something
concrete, a `proposal` — the player can keep replying in plain text as many
times as they like ("give me a different feat instead") to refine the
proposal before ever applying anything; this back-and-forth was an explicit
requirement from the start, not something added later. `LevelUpSession`
(one in-progress conversation per character, enforced by a DB `unique`
constraint on `character_id`) holds the whole message history and the latest
proposal as JSON, resumed on every page load until applied or discarded.

**Two interchangeable backends, picked by one env var — not just Claude.**
`AI_LEVELUP_PROVIDER` (`app/config.py`) is `"anthropic"` (default) or
`"ollama"`, switching the whole app's behavior, not a per-request choice:
- **Anthropic** — the official SDK, model pinned to `claude-haiku-4-5-20251001`
  (not Opus/Fable — Haiku doesn't support the `effort` request parameter,
  learned from a real 400). `ANTHROPIC_API_KEY` required.
- **Ollama / Open WebUI** — a self-hosted server's OpenAI-compatible
  `/chat/completions` endpoint via plain `httpx` (matching `app/pathbuilder.py`'s
  precedent of not reaching for a new SDK for one JSON POST). `OLLAMA_BASE_URL`
  + `OLLAMA_MODEL` required. Local open-weight models are markedly less
  reliable at following a schema, so this path retries once with a sharper
  instruction before giving up, and every provider call is wrapped so a
  failure becomes a plain in-chat error message, never a raw 500.
- **Neither path uses grammar-constrained structured output** (Claude's
  `output_format=`/`output_config.format`, or Ollama's strict
  `response_format.json_schema`) — a real, encountered Claude 400 ("the
  compiled grammar is too large... reduce the number of strict tools") meant
  abandoning that mechanism entirely. Both providers instead get the schema
  as text in the system prompt and their reply is parsed as plain JSON
  (`_parse_turn()`, tolerant of a stray markdown code fence), with the same
  one-retry-then-give-up pattern either way.

**Proposals only ever add — never modify or delete anything that already
exists on the sheet.** This is a bigger restriction than the original plan's
"proposed complete leveled-up sheet," and follows directly from this app's
"copy, don't link" principle (§3): a proposal can update scalar `Character`
fields (level, HP max, AC, DCs, ability scores — never `hp_current` or
`hero_points`) and add brand-new `Spell`/`Equipment`/`Feature`/`Note` rows,
but an existing row is read-only context for the model, never something it
can edit or remove. The system prompt also tells the model to default to a
concrete proposal (using ordinary PF2e defaults for anything unstated, and
saying so in `message`) rather than asking clarifying questions first —
asking first only costs the player a round trip toward whichever provider's
usage limit, when the same guess could just as easily be corrected afterward
like any other row on this sheet. A proposed row never gets a numeric
`attack_bonus` (depends on the wielder's own stats) or `reference_id` (not
sourced from the reference library). A `new_notes` entry — e.g. a "Quick
Combat Reference" summarizing strong action combos — is proposed only when
clearly useful or explicitly asked for, matching the `notes` table's own
long-standing description below.

**The diff view is accept/reject-per-row, not inline editing.** Every
scalar field change and every new row shows a checkbox (checked by default);
refining a proposed value happens by replying in chat, not by hand-editing
the diff. Only the checked subset is ever applied.

**Archive on accept** (`archive_and_apply()` in `app/ai_levelup.py`,
`app/routers/level_up.py`'s `/apply` route): clones the character's current
state (and every child table's current rows) into a new `characters` row
with `is_archived = true` and `parent_character_id` pointing at the live
character's (unchanged) id, *before* applying any accepted change to the live
row — the archive is a real, independent copy, never a re-parented original.
Reuses ordinary character CRUD; no new table needed for the archive itself
(`LevelUpSession` is the only new table, and it's deleted on apply or
discard).
- **Archived characters are genuinely read-only**, not just visually: every
  mutating route (`get_writable_character` in `app/deps.py`, wired into both
  `characters.py`'s own mutating routes and, at the router level in
  `main.py`, all six per-character child routers) 403s against one,
  regardless of who's asking. The sheet template threads a single
  `read_only` flag down through every nested include to hide the matching
  controls (Edit/Delete, tap counters, reorder, Pin, "Book updated" refresh,
  every "+ Add" trigger) — cosmetic backing for that real boundary, not the
  boundary itself.
- Shown as a **History** section on the live character's sheet (e.g. "Level 4
  (archived 2026-09-05)"), linking straight to the ordinary character-view
  route — never in the main character picker. Archives don't chain further;
  it's one level back to the live character only.
- Deleting a live character with archives explicitly deletes its archives
  first — `parent_character_id` has no `ON DELETE CASCADE` (SQLite can't add
  one to an existing column without rebuilding the table), so leaving them in
  place would fail the delete outright with a raw FK-constraint error.

**Per-user permission, independent of `role`.** `User.can_use_ai_levelup`
(admin-granted from `/admin/users`, checked live against the database rather
than the session-cached role so a revoke takes effect immediately, same
guarantee Phase 4b's `is_disabled` gives) gates access alongside the
provider being configured at all — an admin always has access regardless of
the flag. Gated with a 404, not a 403, when either check fails (hide, don't
reveal — matching the `AUTHELIA_USERS_DB_PATH`/set-password precedent).

**Implementation notes:**
- Secrets — `ANTHROPIC_API_KEY`, or `OLLAMA_BASE_URL`/`OLLAMA_MODEL` for the
  local path — same handling as the Entra client secret: env var or secrets
  file on the dataset, never in the image (see Deployment).
- The prompt includes the full sheet (abilities, proficiencies, spells,
  equipment, features, notes) every turn, not just the level number — this is
  a homebrew-heavy game and the note alone never carries enough context
  about what the character already has.
- No background job — every provider call is a synchronous request the
  player waits on, but `start_level_up` commits its new `LevelUpSession`
  *before* making that (potentially 30-120+ second, for a local model) call
  rather than holding it inside an open write transaction — SQLite has only
  one writer for the whole file, and an early version of this held that lock
  across the entire slow call, blocking every other write in the app for the
  duration (reproduced directly, then fixed alongside adding
  `PRAGMA busy_timeout=5000` as a general robustness improvement).

---

## Reality check

Wanderer's Guide, Pathbuilder, and Foundry VTT already do PF2e character
management for free. The reason to build this anyway: the game is homebrew-heavy,
and those tools are built around rules-as-written — they get awkward the moment
the DM grants something off-book. A sheet that never second-guesses what you type,
on your own hardware, is the point. The trade: you give up their automation and
maintained spell/item libraries. Worth it for a homebrew table.