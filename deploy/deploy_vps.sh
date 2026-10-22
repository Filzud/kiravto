#!/usr/bin/env bash
# Выкатка Киравто на VPS. Запуск на сервере: /opt/kiravto/app/deploy/deploy_vps.sh [git-ref]
# Каждое действие пишется в /opt/kiravto/DEPLOY_LOG.md (журнал на сервере).
# Откат — deploy/rollback_vps.sh: возвращает прошлый образ и прошлый коммит, база не трогается.
set -euo pipefail

BASE=/opt/kiravto
APP=$BASE/app
LOG=$BASE/DEPLOY_LOG.md
REF=${1:-main}
COMPOSE=(docker compose -f docker-compose.yml -f deploy/docker-compose.vps.yml)

log() { echo "- $(date -u '+%Y-%m-%d %H:%M:%S UTC') — $*" | tee -a "$LOG"; }

cd "$APP"
echo -e "\n## Выкатка $(date -u '+%Y-%m-%d %H:%M UTC'), ref=$REF" >> "$LOG"
PREV=$(git rev-parse HEAD)
log "текущий коммит $PREV"
git fetch --quiet origin
git checkout --quiet --detach "origin/$REF" 2>/dev/null || git checkout --quiet --detach "$REF"
NEW=$(git rev-parse HEAD)
log "новый коммит $NEW ($(git log -1 --format=%s))"

# прошлый образ сохраняем под тегом rollback — откат без пересборки
if docker image inspect kiravto-collector:latest >/dev/null 2>&1; then
  for s in collector api; do docker tag "kiravto-$s:latest" "kiravto-$s:rollback"; done
  echo "$PREV" > "$BASE/ROLLBACK_COMMIT"
  log "прошлые образы помечены kiravto-*:rollback, коммит для отката $PREV"
fi

if [ ! -f .env ]; then
  # секреты на сервере генерирует сам скрипт; в журнал и вывод значение не попадает
  umask 077
  printf 'API_TOKEN=%s\nTG_PUBLISH_TARGET=test\n' "$(openssl rand -base64 32 | tr -d '/+=' )" > .env
  log ".env создан: только API_TOKEN (сгенерирован на сервере), Telegram и LLM не настроены"
fi

"${COMPOSE[@]}" build --quiet
log "образ собран"
"${COMPOSE[@]}" run --rm --no-deps tests python -m pytest -q -p no:cacheprovider 2>&1 | tail -1 | tee -a "$LOG"
"${COMPOSE[@]}" up -d collector api
log "контейнеры подняты: $("${COMPOSE[@]}" ps --format '{{.Service}}={{.State}}' | tr '\n' ' ')"

for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8080/health >/dev/null 2>&1; then
    log "/health отвечает: $(curl -fsS http://127.0.0.1:8080/health | head -c 300)"
    exit 0
  fi
  sleep 2
done
log "ОШИБКА: /health не ответил за 60 с — откат: deploy/rollback_vps.sh"
exit 1
