#!/usr/bin/env bash
# Rehearses the README's PostgreSQL backup and restore: dump <database> with pg_dump -Fc, restore it
# into <database>_restore with pg_restore, and require the same row count in every table.
# Runs the tools inside <container>, so their version always matches the server.
# Usage: infra/test-restore.sh <postgres container> <database>
set -euo pipefail

container=${1:?postgres container}
source_db=${2:?database}
copy_db="${source_db}_restore"
user=${POSTGRES_USER:-newsintel}
dump=$(mktemp)

pg() { docker exec -i "$container" "$@"; }
psql_on() { pg psql -U "$user" -d "$1" -v ON_ERROR_STOP=1 -Atc "$2"; }
cleanup() { rm -f "$dump"; pg dropdb -U "$user" --if-exists "$copy_db" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

# One line per table, "name count", for every table in public (alembic_version included).
counts() {
  local query
  query=$(psql_on "$1" "select string_agg(format('select %L, count(*) from public.%I', tablename, tablename), ' union all ' order by tablename) from pg_tables where schemaname = 'public'")
  psql_on "$1" "$query order by 1" | tr '|' ' '
}

pg pg_dump -U "$user" -Fc "$source_db" > "$dump"
pg dropdb -U "$user" --if-exists "$copy_db" 2>/dev/null
pg createdb -U "$user" "$copy_db"
pg pg_restore -U "$user" -d "$copy_db" --no-owner --exit-on-error < "$dump"

expected=$(counts "$source_db")
actual=$(counts "$copy_db")
[ -n "$expected" ] || { echo "No tables in $source_db" >&2; exit 1; }
if [ "$expected" != "$actual" ]; then
  echo "Restored row counts differ from $source_db:" >&2
  diff <(echo "$expected") <(echo "$actual") >&2 || true
  exit 1
fi
echo "Restored $(echo "$expected" | wc -l) tables, $(echo "$expected" | awk '{s += $2} END {print s}') rows"
