# Deploying to TrueNAS SCALE (Phase 1)

This doc tracks the deployment story alongside the app — it's phase-dependent,
not a one-time write-up. Phase 1 adds a real SQLite database and an
`uploads/` directory for avatars, so this update wires up persistence via the
**pf2e-sheets** dataset and switches the build to happen directly on the
TrueNAS box (no container registry involved).

## 1. Prerequisites

- TrueNAS SCALE with the **Apps** service enabled (Docker Compose based —
  Kubernetes engine was dropped after Electric Eel).
- The dataset for this app's persistent state already exists:

  **pf2e-sheets**, at `HCNAS\apps\pf2e-sheets`

  Two subdirectories under it back the two compose volumes:
  - `HCNAS\apps\pf2e-sheets\data` → `/data` (the SQLite DB)
  - `HCNAS\apps\pf2e-sheets\uploads` → `/uploads` (avatar images)

  Create them if they don't exist yet:
  ```
  Storage → Datasets → pf2e-sheets → Add Dataset → name: data
  Storage → Datasets → pf2e-sheets → Add Dataset → name: uploads
  ```

## 2. Get the repo onto the box and build there

No registry, no build step on the dev machine — build the image directly on
the TrueNAS box itself.

SSH into the box, then:

```bash
cd /mnt/<pool>/apps/pf2e-sheets   # or wherever you keep app checkouts
git clone https://github.com/<you>/Pathfinder2e.git   # first time
# or, on later phases:
cd Pathfinder2e && git pull

docker build -t pf2e-sheet:latest .
```

`docker-compose.yml` already points at `build: .`, so `docker compose build`
(step 3) does this same on-box build for you — the manual `docker build`
above is just for a quick sanity check while iterating.

## 3a. Deploy via the Apps UI — Custom App (YAML)

1. **Apps → Discover Apps → Custom App** (top right, "Install via YAML" /
   "Custom App").
2. Paste a compose spec based on this repo's [docker-compose.yml](../docker-compose.yml):
   ```yaml
   services:
     sheet:
       build: .
       ports:
         - "8000:8000"
       environment:
         DATABASE_URL: "sqlite:////data/sheet.db"
         UPLOAD_DIR: "/uploads"
       volumes:
         - /mnt/<pool>/apps/pf2e-sheets/data:/data
         - /mnt/<pool>/apps/pf2e-sheets/uploads:/uploads
   ```
   (Swap `<pool>` for your actual pool name — the dataset path above is
   `HCNAS\apps\pf2e-sheets`.)
3. Give the app a name (e.g. `pf2e-sheets`) and deploy.
4. Confirm it's running: **Apps → pf2e-sheets** should show the container as
   *Running*, and `http://<truenas-ip>:8000/healthz` should return
   `{"status":"ok"}`.

## 3b. Alternative — Compose stack alongside other services

If you're already managing other containers on the box via a Compose file
(e.g. through the CLI or Portainer), add this app as another service in that
stack rather than through the Apps UI:

```bash
cd /mnt/<pool>/apps/pf2e-sheets/Pathfinder2e
docker compose up -d --build
```

## 4. Front it with the reverse proxy

Point your existing Traefik/Caddy instance at the container's port 8000 and
attach the usual Let's Encrypt config, e.g. a route for
`sheet.<yourdomain>` → `http://<truenas-ip>:8000`. No app-side TLS config is
needed — the proxy terminates TLS and forwards plain HTTP.

**Stay LAN-only for now.** Phase 1 has no login (that's Phase 2/3) — anyone
who can reach the app can create, edit, and delete characters and upload
files. Don't wire up the public route through the reverse proxy until auth
lands; keep this on an internal-only route or skip the proxy entirely and
hit `http://<truenas-ip>:8000` directly on the LAN.

## 5. Verify

- `http://<truenas-ip>:8000/characters` lists/creates characters and opens a
  full sheet.
- `http://<truenas-ip>:8000/healthz` returns `{"status":"ok"}`.
- Create a character, add an avatar, add a spell/equipment/feature/note,
  then restart the container (`docker compose restart`) and confirm
  everything is still there — proves the two volumes are actually mounted,
  not just that the container runs.

## What's deliberately not here yet

- No env vars for OIDC — auth arrives in Phase 2/3.
- No Authelia service — added alongside local login in Phase 2.

See the root [CLAUDE.md](../CLAUDE.md) for the full build order and the
eventual compose shape once those phases land.
