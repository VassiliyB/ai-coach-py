#!/bin/sh
# Ежедневная резервная копия БД (сервис backup в docker-compose.yml).
#
# Копия это сжатый SQL-дамп в /backups (папка ./backups на хосте), в саму БД ничего не пишется.
# - Раз в сутки после BACKUP_HOUR по поясу TZ; проверка каждый час, поэтому после простоя копия
#   делается при первом запуске в тот же день.
# - Данные не изменились (та же контрольная сумма дампа): новый файл не создаётся.
# - Хранятся последние BACKUP_KEEP копий, старые удаляются.
# - .last_date: дата последней успешной проверки (и при «без изменений»). По ней бот замечает,
#   что копирование остановилось (services/backup_watch.py).
# Разовый запуск: docker compose run --rm backup now
set -u

DIR=/backups
KEEP="${BACKUP_KEEP:-14}"
HOUR="${BACKUP_HOUR:-4}"
mkdir -p "$DIR"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') backup: $*"
}

backup() {
    tmp="$DIR/.dump.sql"
    # Обычный SQL без отметки времени: одинаковые данные дают одинаковый файл и одинаковую сумму
    if ! pg_dump -h db -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges > "$tmp"; then
        rm -f "$tmp"
        log "ОШИБКА: pg_dump не выполнен"
        return 1
    fi
    # pg_dump 16.10+ ставит в начало и конец дампа случайный ключ \restrict: в сумму он не входит
    sum=$(grep -v -E '^\\(un)?restrict ' "$tmp" | sha256sum | cut -d' ' -f1)
    if [ -f "$DIR/.last_sum" ] && [ "$(cat "$DIR/.last_sum")" = "$sum" ]; then
        rm -f "$tmp"
        log "данные не изменились, новая копия не нужна"
        return 0
    fi
    name="coach_$(date +%Y-%m-%d_%H%M).sql.gz"
    if ! gzip -n -c "$tmp" > "$DIR/$name.part"; then
        rm -f "$tmp" "$DIR/$name.part"
        log "ОШИБКА: не удалось сжать дамп"
        return 1
    fi
    mv "$DIR/$name.part" "$DIR/$name"
    rm -f "$tmp"
    echo "$sum" > "$DIR/.last_sum"
    # Ротация: имена с датой сортируются по времени, лишние самые старые удаляются
    ls -1 "$DIR"/coach_*.sql.gz 2>/dev/null | sort -r | tail -n +$((KEEP + 1)) | xargs -r rm -f
    log "копия $name ($(du -h "$DIR/$name" | cut -f1)), хранится копий: $(ls -1 "$DIR"/coach_*.sql.gz | wc -l)"
}

if [ "${1:-}" = "now" ]; then
    backup && date +%F > "$DIR/.last_date"
    exit $?
fi

log "запущен: копия ежедневно после ${HOUR}:00 ($(date +%Z)), хранить ${KEEP}"
while true; do
    today=$(date +%F)
    if [ "$(date +%H)" -ge "$HOUR" ] && [ "$(cat "$DIR/.last_date" 2>/dev/null)" != "$today" ]; then
        if backup; then
            echo "$today" > "$DIR/.last_date"
        fi
    fi
    sleep 3600
done
