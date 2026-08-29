# Deploying to TrueNAS SCALE (Phase 4)

This doc tracks the deployment story alongside the app — it's phase-dependent,
not a one-time write-up. Phase 2 added **login via Authelia** (local accounts,
Argon2, TOTP). Phase 3 added a **second sign-in button, Microsoft Entra**, so
SSO users can log in too — both paths resolve to the same `users` row by
email. Phase 4 adds **multi-user scoping**: each player now sees only their
own characters, and access is gated by an allow-list (§8, below). Entra is
optional — leave its env unset and only the "Sign in with local account"
button shows.

## 1. Prerequisites

- TrueNAS SCALE with the **Apps** service enabled (Docker Compose based —
  Kubernetes engine was dropped after Electric Eel).
- The dataset **pf2e-sheets** at `HCNAS\apps\pf2e-sheets`, with these
  subdirectories backing the compose volumes:
  - `HCNAS\apps\pf2e-sheets\data` → `/data` (the SQLite DB)
  - `HCNAS\apps\pf2e-sheets\uploads` → `/uploads` (avatar images)
  - `HCNAS\apps\pf2e-sheets\authelia` → `/config` (**new in Phase 2** —
    Authelia config, secrets, and its own SQLite DB)

  Create the new one if needed:
  ```
  Storage → Datasets → pf2e-sheets → Add Dataset → name: authelia
  ```

## 2. Get the repo onto the box and build there

No registry, no build step on the dev machine — build the image directly on
the TrueNAS box itself, over SSH:

```bash
cd /mnt/<pool>/apps/pf2e-sheets   # or wherever you keep app checkouts
git clone https://github.com/<you>/Pathfinder2e.git   # first time
cd Pathfinder2e && git pull                            # on later phases

docker build -t pf2e-sheet:latest .
```

**Which deployment method you use from here matters, and picking wrong is
the most common way to get stuck:**

- **§5's `docker compose up -d --build`** (recommended) runs *in this
  checked-out directory* and reads `docker-compose.yml`'s `build: .` itself
  — the manual `docker build` above is then just an optional sanity check,
  since compose does its own build.
- **The Apps UI "Custom App (YAML)"** only ever sees the YAML text you paste
  into it — it has **no access to this git checkout**, so a pasted `build:
  .` has no `Dockerfile` to find and fails with *"failed to read dockerfile:
  open Dockerfile: no such file or directory."* If you're using this method,
  the manual `docker build` above is **required**, not optional — see the
  Apps UI box in §5.

## 3. Generate Authelia's secrets and users

The repo ships `authelia/` as **templates**. Real secrets and password hashes
are generated on the box and are git-ignored — never commit them.

```bash
cd /mnt/<pool>/apps/pf2e-sheets/Pathfinder2e/authelia
mkdir -p secrets

# Random secrets (one per file)
for s in jwt_secret session_secret storage_encryption_key oidc_hmac_secret; do
  docker run --rm authelia/authelia:4.38 authelia crypto rand --length 64 \
    | tail -1 > secrets/$s
done

# OIDC issuer signing key (RSA)
docker run --rm -v "$PWD/secrets:/keys" authelia/authelia:4.38 \
  authelia crypto pair rsa generate --directory /keys
mv secrets/private.pem secrets/oidc_issuer_private_key.pem

# A password hash for each player, pasted into users_database.yml
docker run --rm authelia/authelia:4.38 \
  authelia crypto hash generate argon2 --password 'their-password'

# The app<->Authelia client secret: pick a strong random value, keep the
# PLAINTEXT for the app's env (step 5), and put its HASH in configuration.yml
CLIENT_SECRET=$(docker run --rm authelia/authelia:4.38 authelia crypto rand --length 48 | tail -1)
echo "client secret (app env): $CLIENT_SECRET"
docker run --rm authelia/authelia:4.38 \
  authelia crypto hash generate pbkdf2 --variant sha512 --password "$CLIENT_SECRET"
```

Then edit the templates (swap `example.com` for your domain throughout):
- `authelia/configuration.yml` — set the `session.cookies` domain/URLs, the
  client `redirect_uris`, and paste the **client-secret hash**.
- `authelia/users_database.yml` — one entry per player with their argon2 hash
  and real email. **The email must match** what you'll allow-list in Phase 4.

## 4. Reverse proxy — two routes under one parent domain

The session cookie is scoped to the parent domain, so the app and Authelia
must share it. Add both routes to your existing Traefik/Caddy (Let's Encrypt
as usual — the proxy terminates TLS and forwards plain HTTP):

```
sheet.<yourdomain>  →  sheet:8000
auth.<yourdomain>   →  authelia:9091
```

## 5. Deploy

Set the app's environment (a `.env` beside the compose file is git-ignored):

```bash
# .env
SESSION_SECRET=<run: openssl rand -hex 32>
APP_BASE_URL=https://sheet.example.com
OIDC_AUTHELIA_ISSUER=https://auth.example.com
OIDC_AUTHELIA_CLIENT_ID=pf2e-sheet
OIDC_AUTHELIA_CLIENT_SECRET=<the PLAINTEXT client secret from step 3>
```

Then bring the stack up (app + Authelia together):

```bash
cd /mnt/<pool>/apps/pf2e-sheets/Pathfinder2e
docker compose up -d --build
```

The compose file mounts `./data`, `./uploads`, and `./authelia` from the
dataset — this SSH/CLI method is **recommended**: `--build` rebuilds from
your latest `git pull` every time, and the env vars above load automatically
from `.env`.

> **Using the Apps UI "Custom App (YAML)" instead?** It cannot build an
> image — it only receives the YAML text, with no access to this git
> checkout — so `build: .` fails there with *"open Dockerfile: no such file
> or directory."* You must build manually over SSH first (§2's `docker
> build -t pf2e-sheet:latest .`), then paste YAML that references the tag
> instead of building:
> ```yaml
> services:
>   sheet:
>     image: pf2e-sheet:latest   # not "build: ."
>     ports:
>       - "8101:8000"   # host:container — only the host side needs to change if 8000 is taken
>     environment:
>       DATABASE_URL: "sqlite:////data/sheet.db"
>       UPLOAD_DIR: "/uploads"
>       SESSION_SECRET: "<same as .env above>"
>       APP_BASE_URL: "https://sheet.example.com"
>       OIDC_AUTHELIA_ISSUER: "https://auth.example.com"
>       OIDC_AUTHELIA_CLIENT_ID: "pf2e-sheet"
>       OIDC_AUTHELIA_CLIENT_SECRET: "<the PLAINTEXT client secret from step 3>"
>     volumes:
>       - /mnt/<pool>/apps/pf2e-sheets/data:/data
>       - /mnt/<pool>/apps/pf2e-sheets/uploads:/uploads
> ```
> Authelia needs its own Custom App the same way, using `image:
> authelia/authelia:4.38` (that one was never the problem — it's pulled from
> Docker Hub, not built) with `/mnt/<pool>/apps/pf2e-sheets/authelia:/config`
> mounted.
>
> **The rebuild workflow differs too**: after every `git pull`, re-run
> `docker build -t pf2e-sheet:latest .` over SSH, then redeploy/restart the
> Custom App from the UI so it picks up the new image — the Apps UI has no
> equivalent of `--build`, it never rebuilds on its own. This is the
> tradeoff for this method; the CLI path above doesn't have this extra step.

## 6. Enrol TOTP and verify

1. Visit `https://sheet.example.com` → you're bounced to the app's `/login`.
2. Click **Sign in with local account** → Authelia asks for the password, then
   to **enrol an authenticator app** (TOTP). Scan the QR with your app.
3. After the second factor you land back on the character list, signed in —
   the nav shows your name and a **Log out** button.
4. `docker compose restart` and reload — you should still be signed in (the
   session cookie is signed with `SESSION_SECRET`) and your data intact
   (proves the three volumes are mounted).
5. `http://<truenas-ip>:8101/healthz` still returns `{"status":"ok"}` without
   a login (it's intentionally open for the container healthcheck).

Because real login now exists, it's safe to expose the app through the public
reverse-proxy route — but note the gate is "any user a provider authenticates":
anyone in `users_database.yml`, plus (once Entra is added below) anyone in the
tenant/accounts you allow there. Per-user character scoping, an app-level
allow-list, and the admin role arrive in **Phase 4** (below).

## 7. Add Microsoft Entra (optional — Phase 3)

Adds the "Sign in with Microsoft" button. Skip this section to keep local-only
login; the button only appears when all three `OIDC_ENTRA_*` vars are set.

1. **Register the app** in Entra: *Microsoft Entra admin center → App
   registrations → New registration*.
   - Supported account types: whichever fits your table (single-tenant is
     simplest and most restrictive).
   - **Redirect URI** (type *Web*): `https://sheet.example.com/auth/entra/callback`.
2. **Client secret**: *Certificates & secrets → New client secret*. Copy the
   **Value** (not the ID) — this is `OIDC_ENTRA_CLIENT_SECRET`.
3. **Expose the email claim** so the app gets a real address: *Token
   configuration → Add optional claim → ID → `email`*. Entra's `email` isn't
   guaranteed otherwise; the app falls back to the account's UPN
   (`preferred_username`), but adding the claim is more reliable. Whatever it
   returns **must match the person's Authelia email** so both buttons resolve
   to the same character list.
4. **Issuer** is tenant-specific, **not** `common`:
   `https://login.microsoftonline.com/<tenant-id>/v2.0` — the app validates the
   token issuer against this, so `common` would fail.
5. Add to the app's `.env` (alongside the Authelia vars) and redeploy:
   ```bash
   OIDC_ENTRA_ISSUER=https://login.microsoftonline.com/<tenant-id>/v2.0
   OIDC_ENTRA_CLIENT_ID=<application (client) id>
   OIDC_ENTRA_CLIENT_SECRET=<the secret Value from step 2>
   ```
   `docker compose up -d` — the login page now shows both buttons. No Authelia
   change is needed; Entra talks straight to Microsoft.

Verify: sign in with Microsoft and land on the character list. If the same
person already logged in via a local account with the same email, they get the
**same** characters — one `users` row, two ways in.

## 8. Multi-user & the allow-list (Phase 4)

Each player now sees only their own characters; access is gated by an
**allow-list** — a login only works for an email already present in the
app's `users` table.

- **The very first person to ever log in becomes admin**, and that login is
  the *only* one that auto-creates itself — every login after that requires
  the email to already be registered. This means there's a real race: if the
  app is reachable before you've logged in yourself, whoever gets there
  first becomes admin. **Log in immediately after first bringing the stack
  up, before sharing the URL with anyone.**
- To add a player: as the admin, visit `/admin/users` and add their email.
  It must match the email their provider (Authelia or Entra) will send —
  same rule as step 7's Entra note. They can then sign in with either
  provider.
- Someone not on the list gets a plain "your account isn't registered"
  message at `/login` rather than a session — nothing is created for them.
- The admin sees every character (with an owner label on each); everyone
  else sees only their own. There's currently no UI to promote another
  account to admin or remove someone from the list — just the one bootstrap
  admin and the add-email form.

See the root [CLAUDE.md](../CLAUDE.md) for the full build order.
