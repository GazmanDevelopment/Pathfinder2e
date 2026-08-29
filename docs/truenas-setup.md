# Deploying to TrueNAS SCALE (Phase 0)

This covers only the Phase 0 scaffold: the bare FastAPI container running and
reachable behind the existing reverse proxy. No database, uploads volume, or
auth are wired up yet — those arrive in later phases and this doc will grow
with them.

## 1. Prerequisites

- TrueNAS SCALE with the **Apps** service enabled (Docker Compose based —
  Kubernetes engine was dropped after Electric Eel).
- A dataset for this app's persistent state, e.g. `/mnt/<pool>/apps/sheet`.
  Nothing needs to live there yet in Phase 0, but create it now so later
  phases (SQLite DB, `uploads/`) don't require re-plumbing:
  ```
  Storage → Datasets → <pool> → Add Dataset → name: sheet
  ```
- A container registry the TrueNAS box can pull from (GHCR, Docker Hub, or a
  local registry). Push the image there from your build machine, or build
  directly on the box if you'd rather skip a registry for now.

## 2. Build and publish the image

From the repo root, on your build machine:

```bash
docker build -t ghcr.io/<you>/sheet-app:latest .
docker push ghcr.io/<you>/sheet-app:latest
```

(Swap in your own registry/tag. If TrueNAS can reach your repo directly, you
can instead point Compose at a local build context and skip pushing — see
step 3b.)

## 3a. Deploy via the Apps UI — Custom App (YAML)

1. **Apps → Discover Apps → Custom App** (top right, "Install via YAML" /
   "Custom App").
2. Paste a compose spec based on this repo's [docker-compose.yml](../docker-compose.yml):
   ```yaml
   services:
     sheet:
       image: ghcr.io/<you>/sheet-app:latest
       ports:
         - "8000:8000"
   ```
3. Give the app a name (e.g. `pf2e-sheet`) and deploy.
4. Confirm it's running: **Apps → pf2e-sheet** should show the container as
   *Running*, and `http://<truenas-ip>:8000/healthz` should return
   `{"status":"ok"}`.

## 3b. Alternative — Compose stack alongside other services

If you're already managing other containers on the box via a Compose file
(e.g. through the CLI or Portainer), add this app as another service in that
stack rather than through the Apps UI. Same `docker-compose.yml`, just
deployed with `docker compose up -d` instead of the GUI import.

## 4. Front it with the reverse proxy

Point your existing Traefik/Caddy instance at the container's port 8000 and
attach the usual Let's Encrypt config, e.g. a route for
`sheet.<yourdomain>` → `http://<truenas-ip>:8000`. No app-side TLS config is
needed — the proxy terminates TLS and forwards plain HTTP.

## 5. Verify

- `https://sheet.<yourdomain>/` renders the "Phase 0 scaffold is running"
  page.
- `https://sheet.<yourdomain>/healthz` returns `{"status":"ok"}`.

That's the whole Phase 0 goal: prove the build → registry → TrueNAS →
reverse-proxy pipeline works end to end before any real features land on
top of it.

## What's deliberately not here yet

- No volume mount — there's no DB or `uploads/` dir to persist yet.
- No env vars for OIDC — auth arrives in Phase 2/3.
- No Authelia service — added alongside local login in Phase 2.

See the root [CLAUDE.md](../CLAUDE.md) for the full build order and the
eventual compose shape once those phases land.
