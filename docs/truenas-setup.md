# Deploying to TrueNAS SCALE (Phase 2)

This doc tracks the deployment story alongside the app — it's phase-dependent,
not a one-time write-up. Phase 2 adds **login via Authelia** (the app is an
OIDC client; Authelia owns usernames, Argon2 password hashes, and TOTP), so
this update adds the Authelia service, its config and secrets, and the second
reverse-proxy route. The app now **requires a signed-in session** to reach any
character page.

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
the TrueNAS box itself.

```bash
cd /mnt/<pool>/apps/pf2e-sheets   # or wherever you keep app checkouts
git clone https://github.com/<you>/Pathfinder2e.git   # first time
cd Pathfinder2e && git pull                            # on later phases

docker build -t pf2e-sheet:latest .
```

`docker-compose.yml` points at `build: .`, so `docker compose build` does this
same on-box build for you — the manual `docker build` is just a sanity check.

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
dataset. If you deploy via the Apps UI **Custom App (YAML)** instead, use
absolute dataset paths for the volumes (e.g.
`/mnt/<pool>/apps/pf2e-sheets/authelia:/config`) and set the same env there.

## 6. Enrol TOTP and verify

1. Visit `https://sheet.example.com` → you're bounced to the app's `/login`.
2. Click **Sign in with local account** → Authelia asks for the password, then
   to **enrol an authenticator app** (TOTP). Scan the QR with your app.
3. After the second factor you land back on the character list, signed in —
   the nav shows your name and a **Log out** button.
4. `docker compose restart` and reload — you should still be signed in (the
   session cookie is signed with `SESSION_SECRET`) and your data intact
   (proves the three volumes are mounted).
5. `http://<truenas-ip>:8000/healthz` still returns `{"status":"ok"}` without
   a login (it's intentionally open for the container healthcheck).

Because real login now exists, it's safe to expose the app through the public
reverse-proxy route — but note the Phase 2 gate is "any user Authelia
authenticates," i.e. anyone in `users_database.yml`. Per-user character
scoping, an app-level allow-list, and the admin role arrive in **Phase 4**.

## What's deliberately not here yet

- No Microsoft Entra ("Sign in with Microsoft") — that's Phase 3. The login
  page and routes are already generic over providers, so it's a config add.
- No per-user scoping / allow-list / admin — Phase 4. Every signed-in user
  currently sees every character.

See the root [CLAUDE.md](../CLAUDE.md) for the full build order.
