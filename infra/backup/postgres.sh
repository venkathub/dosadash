#!/usr/bin/env bash
# Create a local, permission-restricted PostgreSQL archive. Install schedule explicitly.
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_DIR="${DOSADASH_BACKUP_DIR:-/opt/dosadash-backups}"
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
exec 9>"$BACKUP_DIR/.lock"
flock -n 9 || { echo 'Backup already running' >&2; exit 1; }
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TEMP="$(mktemp "$BACKUP_DIR/.postgres.XXXXXX")"
trap 'rm -f "$TEMP"' EXIT
COMPOSE=(docker compose --env-file "$ROOT/infra/.env" -f "$ROOT/infra/docker-compose.yml")
"${COMPOSE[@]}" exec -T postgres sh -c \
  'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$TEMP"
# Archive readability is useful, but is NOT a restore drill.
"${COMPOSE[@]}" exec -T postgres pg_restore --list < "$TEMP" > /dev/null
FINAL="$BACKUP_DIR/postgres-$STAMP.dump"
test ! -e "$FINAL" || { echo "Archive name already exists" >&2; exit 1; }
mv "$TEMP" "$FINAL"
sha256sum "$FINAL" > "$FINAL.sha256"
# Rotate only after the new archive is complete. Never touch unrelated files.
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'postgres-*.dump*' -mtime +7 -delete
printf 'Backup completed: %s\n' "$FINAL"
