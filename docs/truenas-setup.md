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
- The dataset **pf2e-sheets** at `HCNAS\apps\pf2e-sheets` — step 2 clones the
  repo directly onto it, at `HCNAS\apps\pf2e-sheets\Pathfinder2e`. The
  persistent volumes (`data`, `uploads`, `authelia`) live as plain
  subdirectories **inside that checkout**, not as separate child datasets —
  `Pathfinder2e\data` → `/data`, `Pathfinder2e\uploads` → `/uploads`,
  `Pathfinder2e\authelia` → `/config`. All three still live on the
  `pf2e-sheets` dataset (the checkout is just a subdirectory of it), so a
  snapshot of `pf2e-sheets` backs up all of it together — you don't get
  independent per-volume snapshot granularity this way, but it means the
  path a container mounts and the path you actually edited files in (step 3)
  are always the same directory. Getting those two out of sync is a real,
  easy mistake — it's exactly what makes Authelia start with no config,
  silently generate a default, and immediately exit.

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

If `docker` needs `sudo` on your box, do **not** put `sudo` in front of the
`for` loop below — `sudo` runs a single program, it doesn't understand shell
keywords like `for`/`do`, and `sudo for ...; do` fails with a shell parse
error before `docker` is ever involved. Either drop `sudo` (try one `docker
run ...` command first — if it works without it, your user's already in the
`docker` group), or wrap the whole loop as one argument: `sudo sh -c '...'`.

```bash
cd /mnt/<pool>/apps/pf2e-sheets/Pathfinder2e/authelia
mkdir -p secrets

# Random secrets (one per file)
for s in jwt_secret session_secret storage_encryption_key oidc_hmac_secret; do
  docker run --rm authelia/authelia:4.38 authelia crypto rand --length 64 \
    | tail -1 > secrets/$s
done
```

If that loop needs `sudo`, use this instead (same four secrets, no loop to
fight with `sudo` over):
```bash
sudo sh -c 'for s in jwt_secret session_secret storage_encryption_key oidc_hmac_secret; do
  docker run --rm authelia/authelia:4.38 authelia crypto rand --length 64 | tail -1 > secrets/$s
done'
```

```bash
# OIDC issuer signing key (RSA)
docker run --rm -v "$PWD/secrets:/keys" authelia/authelia:4.38 \
  authelia crypto pair rsa generate --directory /keys
mv secrets/private.pem secrets/oidc_issuer_private_key.pem

# A password hash for each player who'll use a LOCAL account. Unlike every
# other secret in this block, this one is NOT one-time: run it again, and
# add another entry to users_database.yml, every time a new player joins
# or an existing one changes their password. Entra/SSO players skip this
# entirely — their password lives in your Microsoft tenant, not here.
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
  client `redirect_uris`, and paste the **client-secret hash**. Also paste
  the full contents of `secrets/oidc_issuer_private_key.pem` inline at
  `identity_providers.oidc.jwks[0].key` — that field is the one exception
  that does **not** accept the `@/path` file-reference notation the other
  secrets use; giving it the path string instead of the key's actual
  content fails with *"no PEM block was supplied or it was malformed."*
- `authelia/users_database.yml` — one entry per player with their argon2 hash
  and real email. **The email must match** what you'll allow-list in Phase 4.

## 4. Reverse proxy — two routes under one parent domain

Authelia's session cookie is scoped to a parent domain, and genuinely does
not work with a bare IP address — even for LAN-only testing, Authelia needs
a real hostname to set its own cookie against. So this step isn't optional,
even before you're ready to expose anything publicly.

**Where your reverse proxy runs changes how it reaches these containers:**

- **A proxy that's itself a container in this compose project** (e.g.
  Traefik/Caddy added as another service here) can reach them by Docker's
  internal network name and port — `sheet:8000`, `authelia:9091` — without
  either needing a published host port.
- **A proxy on a separate device** — this repo's `docker-compose.yml`
  assumes this case, since it's the common one for a home NAS (a Synology
  DSM reverse proxy, pfSense/OPNsense, or any proxy that isn't itself a
  container here). It can only reach these services over the network, via
  the **published host ports** (`8101` for the app, `9091` for Authelia —
  both are `ports:`, not `expose:`, in the compose file for exactly this
  reason). Point it at `<this-host-ip>:8101` and `<this-host-ip>:9091`.

Either way, you need two hostnames sharing one parent domain, e.g.:
```
sheet.<yourdomain>  →  sheet:8000            (same-host proxy)
auth.<yourdomain>   →  authelia:9091

pathfinder.example.com      →  <host-ip>:8101   (separate-device proxy)
auth.pathfinder.example.com →  <host-ip>:9091
```

**Synology DSM specifically** (Control Panel → Login Portal → Advanced →
Reverse Proxy, or Application Portal → Reverse Proxy on newer DSM): create
one rule per hostname, source port 443 (HTTPS) → destination the host IP
and the relevant published port (HTTP — the proxy terminates TLS, the
containers don't need certs). In each rule's **Custom Header** tab, confirm
`X-Forwarded-Proto: https` is being sent — DSM doesn't always add this
automatically, and without it the app sees every request as plain HTTP even
though the browser used HTTPS, which silently breaks the session cookie (see
`SESSION_HTTPS_ONLY` in step 5). Certificates for both hostnames can be free
Let's Encrypt certs issued directly in DSM (Control Panel → Security →
Certificate) and assigned to each rule.

## 5. Deploy

Set the app's environment (a `.env` beside the compose file is git-ignored).
`APP_BASE_URL` and `OIDC_AUTHELIA_ISSUER` are hardcoded directly in
`docker-compose.yml` (not read from `.env`) since this deployment's domain
is committed as-is — only actual secrets need to go here:

```bash
# .env
SESSION_SECRET=<run: openssl rand -hex 32>
OIDC_AUTHELIA_CLIENT_ID=pf2e-sheet
OIDC_AUTHELIA_CLIENT_SECRET=<the PLAINTEXT client secret from step 3>
```

Don't set `SESSION_HTTPS_ONLY` — leave it at its default (`true`). The proxy
in step 4 terminates real TLS, so the browser-to-proxy hop is genuinely
HTTPS; forcing this off is only for plain-HTTP LAN testing with no proxy at
all, and would weaken the session cookie unnecessarily here.

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
> instead of building.
>
> **Use one Custom App for both services** — confirmed against TrueNAS's own
> docs, a Custom App accepts a normal multi-service `services:` block just
> like `docker-compose.yml`, so paste `sheet` and `authelia` together as
> below rather than creating two separate apps. (Splitting them into two is
> possible if you'd rather stop/restart/view logs for each independently
> from the Apps UI, but isn't required — this stack's server-to-server calls
> go over the public hostnames through the reverse proxy either way, not
> Docker's internal network, so there's no dependency forcing a split.)
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
>       APP_BASE_URL: "https://pathfinder.huscroft.com.au"
>       OIDC_AUTHELIA_ISSUER: "https://auth.pathfinder.huscroft.com.au"
>       OIDC_AUTHELIA_CLIENT_ID: "pf2e-sheet"
>       OIDC_AUTHELIA_CLIENT_SECRET: "<the PLAINTEXT client secret from step 3>"
>     volumes:
>       - /mnt/<pool>/apps/pf2e-sheets/Pathfinder2e/data:/data
>       - /mnt/<pool>/apps/pf2e-sheets/Pathfinder2e/uploads:/uploads
>
>   authelia:
>     image: authelia/authelia:4.38   # pulled from Docker Hub, not built — never the problem
>     environment:
>       TZ: "Australia/Sydney"
>     ports:
>       - "9091:9091"   # published, not just internal — see §4
>     volumes:
>       # Must be the checkout's own authelia/ — the same directory step 3
>       # generated secrets and edited configuration.yml in. Point this
>       # anywhere else and Authelia finds an empty directory, silently
>       # writes a default config, and exits — no config error, just a
>       # container that won't stay up.
>       - /mnt/<pool>/apps/pf2e-sheets/Pathfinder2e/authelia:/config
> ```
>
> **The rebuild workflow differs too**: after every `git pull`, re-run
> `docker build -t pf2e-sheet:latest .` over SSH, then redeploy/restart the
> Custom App from the UI so it picks up the new image — the Apps UI has no
> equivalent of `--build`, it never rebuilds on its own. This is the
> tradeoff for this method; the CLI path above doesn't have this extra step.
> It also won't appear in TrueNAS's Apps list at all if you use the CLI path
> instead — that's purely a `docker compose` project the Apps UI doesn't
> know about, so pick whichever tradeoff (GUI visibility vs. simpler
> rebuilds) matters more to you before committing to one method.

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
  same rule as step 7's Entra note.
  - **If they're using a local account**, this app-side allow-list entry is
    only half of it — they also need a **separate** entry in Authelia's own
    `authelia/users_database.yml` (step 3, above) with their own password
    hash, then a `docker compose restart authelia`. Two systems, two
    additions, for every new local-account player.
  - **If they're using Entra/SSO**, the allow-list entry above is the only
    step — nothing to add on the Authelia side.
- Someone not on the list gets a plain "your account isn't registered"
  message at `/login` rather than a session — nothing is created for them.
- The admin sees every character (with an owner label on each); everyone
  else sees only their own. There's currently no UI to promote another
  account to admin or remove someone from the list — just the one bootstrap
  admin and the add-email form.

See the root [CLAUDE.md](../CLAUDE.md) for the full build order.
