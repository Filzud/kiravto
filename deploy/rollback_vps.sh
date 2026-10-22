#!/usr/bin/env bash
# Откат Киравто на VPS к прошлой выкатке: прошлые образы (kiravto-*:rollback) и прошлый коммит.
# База в томе kiravto-data не трогается: схема только дополняется (ALTER TABLE ADD COLUMN, CREATE IF NOT EXISTS),
# прошлая версия кода новые колонки и таблицы просто не читает.
set -euo pipefail

BASE=/opt/kiravto
APP=$BASE/app
LOG=$BASE/DEPLOY_LOG.md
COMPOSE=(docker compose -f docker-compose.yml -f deploy/docker-compose.vps.yml)

log() { echo "- $(date -u '+%Y-%m-%d %H:%M:%S UTC') — $*" | tee -a "$LOG"; }

cd "$APP"
[ -f "$BASE/ROLLBACK_COMMIT" ] || { echo "нет прошлой выкатки — откатывать некуда"; exit 1; }
PREV=$(cat "$BASE/ROLLBACK_COMMIT")
echo -e "\n## Откат $(date -u '+%Y-%m-%d %H:%M UTC')" >> "$LOG"
log "было $(git rev-parse HEAD), возвращаю $PREV"
git checkout --quiet --detach "$PREV"
for s in collector api; do docker tag "kiravto-$s:rollback" "kiravto-$s:latest"; done
"${COMPOSE[@]}" up -d --no-build collector api
sleep 5
log "после отката: $(curl -fsS http://127.0.0.1:8080/health 2>&1 | head -c 200)"
