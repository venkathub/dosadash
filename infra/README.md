# infra/ — deploy notes

## One-time VPS bootstrap (AIC Cloud, 4 GB RAM)

```bash
# as a sudo-capable user on the VPS
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git curl
sudo mkdir -p /opt/dosadash && sudo chown $USER /opt/dosadash
git clone git@github.com:<you>/dosadash.git /opt/dosadash
cd /opt/dosadash
cp infra/.env.example infra/.env   # then edit: POSTGRES_PASSWORD (HTTP_PORT defaults to 8080)
docker compose -f infra/docker-compose.yml --env-file infra/.env up -d --build
curl -fsS http://localhost:8080/healthz
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

## Routing

Public TLS terminates at the **AIC Cloud front proxy** (ports 80/443 are
provider-reserved). The AIC Domain panel forwards `dosadash.venkateshs.dev`
to this VPS on `HTTP_PORT` (default **8080**), where Caddy does path routing
over plain HTTP:

- `/api/*`, `/ws/*`, `/healthz` → `api:8000`
- `/ai/*` → `ai:8001` (path stripped)
- everything else → `web:3000` (`/`, `/kds`, `/admin`)

Local smoke test on the VPS: `curl -fsS http://localhost:8080/healthz`
