# Authelia secrets

Real secret files live in this directory on the box and are **git-ignored** —
nothing here should ever be committed. `configuration.yml` references them by
path (`@/config/secrets/...`).

Generate them on the TrueNAS box (see `docs/truenas-setup.md` for the full
walk-through):

| File | How to generate |
|---|---|
| `jwt_secret` | `authelia crypto rand --length 64` |
| `session_secret` | `authelia crypto rand --length 64` |
| `storage_encryption_key` | `authelia crypto rand --length 64` |
| `oidc_hmac_secret` | `authelia crypto rand --length 64` |
| `oidc_issuer_private_key.pem` | `authelia crypto certificate rsa generate --directory /config/secrets` (use the key) |
| `smtp_password` | Not generated — it's your email provider's credential (Mailjet's **Secret Key**, from the same API Keys page as the username). See `docs/truenas-setup.md` §9. |

The app's own secrets (its `SESSION_SECRET` and the `pf2e-sheet` client secret,
whose **hash** goes in `configuration.yml`) are set as environment variables on
the `sheet` service — see the `.env` described in `docs/truenas-setup.md`.
