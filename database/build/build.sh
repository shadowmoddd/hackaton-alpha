#!/usr/bin/env bash
# Пересборка БД из исходного дампа. Нужны: PostgreSQL 16+ (с contrib), pg_restore версии >= 17 (дамп сделан pg_dump 17), python3 + psycopg2.
# PGHOST/PGPORT/PGUSER задаются переменными окружения. Использование: ./build.sh исходный.backup alfa
set -euo pipefail
SRC="$1"; DB="${2:-alfa}"; D="$(cd "$(dirname "$0")" && pwd)"
dropdb --if-exists "$DB"; createdb "$DB"
pg_restore -d "$DB" --no-owner --no-privileges "$SRC" || true   # 1 безобидная ошибка: SET transaction_timeout на PG16
psql -v ON_ERROR_STOP=1 -q -d "$DB" -f "$D/01_materials.sql"
python3 "$D/02_chunks.py" "$DB"
psql -v ON_ERROR_STOP=1 -q -d "$DB" -f "$D/03_indexes_bot.sql"
