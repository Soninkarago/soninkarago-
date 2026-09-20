#!/usr/bin/env sh
set -eu
: "${RESTORE_DATABASE_URL:?RESTORE_DATABASE_URL manquant}"
: "${1:?Usage: restore_postgres.sh <dump>}"
pg_restore --clean --if-exists --no-owner --no-privileges --dbname="$RESTORE_DATABASE_URL" "$1"
echo "Restauration terminée. Vérifiez /api/ready et l’intégrité avant toute bascule."
