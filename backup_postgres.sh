#!/usr/bin/env sh
set -eu
: "${DATABASE_URL:?DATABASE_URL manquant}"
mkdir -p backups
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="backups/soninkarago-${STAMP}.dump"
pg_dump --format=custom --no-owner --no-privileges "$DATABASE_URL" > "$OUT"
echo "$OUT"
