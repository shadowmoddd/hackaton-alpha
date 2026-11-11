#!/bin/bash
# Выполняется один раз при первом создании тома. Восстанавливает готовую БД.
set -e
pg_restore --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB" /dump/alfa_ready.dump
echo "alfa db restored"
