# infra/ — deploy notes

## One-time VPS bootstrap (AIC Cloud, 4 GB RAM)

```bash
# as a sudo-capable user on the VPS
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git curl
sudo mkdir -p /opt/dosadash && sudo chown $USER /opt/dosadash
git clone git@github.com:<you>/dosadash.git /opt/dosadash
cd /opt/dosadash
cp infra/.env.example infra/.env   # then edit: POSTGRES_PASSWORD, DOMAIN
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d --build
curl -fsS http://localhost/healthz
```

## GitHub Actions secrets (repo → Settings → Environments → production)

| Secret | Value |
|---|---|
| `SSH_HOST` | VPS IP or hostname |
| `SSH_USER` | deploy user |
| `SSH_KEY`  | private key (dedicated deploy key) |
| `SSH_PORT` | 22 (or custom) |

After that, every merge to `main` deploys automatically (`.github/workflows/deploy.yml`).
Rollback = revert PR on `main` (docs/08).

## Routing (Caddyfile)

- `/api/*`, `/ws/*`, `/healthz` → `api:8000`
- `/ai/*` → `ai:8001` (path stripped)
- everything else → `web:3000` (`/`, `/kds`, `/admin`)
- Set `DOMAIN=yourdomain.com` in `infra/.env` for automatic HTTPS; default `:80` serves plain HTTP so the stack works before DNS exists.
