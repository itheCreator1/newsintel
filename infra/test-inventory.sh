#!/bin/sh
# Read-only inventory of the full-gate test selection, used to prove phase-5 equivalence between
# the baseline and any optimized gate: backend pytest collection, frontend Vitest list, and
# per-group Playwright specs/e2e() invocations. --no-deps throughout -- starts nothing but the two
# test-runner images, no database/search/fixture containers.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
. "$root/infra/lib.sh"
project=$(ni_project inventory)
compose="docker compose -p $project -f docker/compose.test.yaml"
ni_report_init "${NEWSINTEL_TEST_ARTIFACTS:-}" "$root" inventory

cleanup() {
  status=$?
  ni_report_finalize "$status"
  $compose down -v --remove-orphans >/dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

cd "$root"
mkdir -p "$artifacts/inventory"

# ni_e2e_calls <group>: the ordered e2e() grep-argument strings from that group's own case arm in
# test-e2e.sh (the second `case $group in` block -- the first is the earlier `users ...` dispatch,
# which also has a "<group>)" line and would otherwise be matched first). A deliberately repeated
# call (e.g. search's two "search restores URL state" invocations) prints twice, not deduplicated.
ni_e2e_calls() {
  awk -v grp="$1" '
    /^case \$group in$/ { casenum++ }
    casenum == 2 && $0 ~ "^  " grp "\\)" { inblock = 1 }
    casenum == 2 && inblock { print }
    casenum == 2 && inblock && /;;/ { inblock = 0 }
  ' "$root/infra/test-e2e.sh" | grep -oE 'e2e "[^"]*"' | sed -e 's/^e2e "//' -e 's/"$//'
}

ni_stage build
$compose build backend-test frontend-test

ni_stage backend.full
$compose run --rm --no-deps backend-test pytest --collect-only -q tests \
  > "$artifacts/inventory/backend-full.txt"

ni_stage backend.quick
if [ -f "$root/backend/tests/classify.py" ]; then
  # ponytail: classify.py's manifest-selection CLI is defined by Task 3, not yet landed -- this
  # guard fires once it exists but the actual unit-path collection command isn't wired in yet;
  # upgrade path is Task 3 (or 5) adding the real invocation here. Until then, no file is written,
  # matching this task's brief ("or just omit that file for now").
  echo "backend/tests/classify.py exists but backend-quick.txt is not yet wired (Task 3+ follow-up)" >&2
fi

ni_stage frontend.tests
$compose run --rm --no-deps frontend-test npx vitest list > "$artifacts/inventory/frontend-tests.txt"

for group in search investigations monitors graph; do
  ni_stage "e2e.$group.invocations"
  ni_e2e_calls "$group" > "$artifacts/inventory/e2e-$group-invocations.txt"

  ni_stage "e2e.$group.specs"
  filter=$(tr '\n' '|' < "$artifacts/inventory/e2e-$group-invocations.txt" | sed 's/|$//')
  $compose run --rm --no-deps frontend-test npx playwright test --list --reporter=line --grep "$filter" \
    > "$artifacts/inventory/e2e-$group-specs.txt"
done

echo "Inventory written: $artifacts/inventory"
