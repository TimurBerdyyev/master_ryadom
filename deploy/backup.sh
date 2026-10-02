#!/usr/bin/env bash
# Daily backup of the database and uploaded photos; keeps the last 14 days.
#   crontab -e  →  30 3 * * * /opt/master_ryadom/deploy/backup.sh >> /var/log/master_ryadom_backup.log 2>&1
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${BACKUP_DIR:-$ROOT/backups}"
STAMP="$(date +%Y-%m-%d_%H%M)"
mkdir -p "$DEST"
cd "$ROOT"

docker compose exec -T db pg_dump -U master_ryadom -d master_ryadom --format=custom > "$DEST/db_$STAMP.dump"
docker compose run --rm --no-deps -T -v "$DEST":/backup backend \
  tar czf "/backup/uploads_$STAMP.tar.gz" -C /app uploads

find "$DEST" -name 'db_*.dump' -mtime +14 -delete
find "$DEST" -name 'uploads_*.tar.gz' -mtime +14 -delete
echo "$(date '+%F %T') backup OK → $DEST"

# Restore:
#   docker compose exec -T db pg_restore -U master_ryadom -d master_ryadom --clean < backups/db_<date>.dump
#   docker compose run --rm --no-deps -T -v "$DEST":/backup backend tar xzf /backup/uploads_<date>.tar.gz -C /app
