#!/bin/sh
# Read-only inventory of the full-gate test selection, used to prove phase-5 equivalence between
# the baseline and any optimized gate: backend pytest collection, frontend Vitest list, and
# per-group Playwright specs/e2e() invocations. --no-deps throughout -- starts nothing but the two
# test-runner images, no database/search/fixture containers.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
. "$root/infra/lib.sh"
project=$(ni_project inventory)
# Run-scoped tags, same convention as test-quick.sh: never fall back to the shared :local tag,
# so a concurrent run can't clobber this run's images (or vice versa).
export NEWSINTEL_IMAGE_BACKEND_TEST="newsintel-backend-test:$project"
export NEWSINTEL_IMAGE_FRONTEND_TEST="newsintel-frontend-test:$project"
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
# test-e2e.sh (the third `case $group in` block -- the first is the group-validation dispatch
# (search|investigations|monitors|graph) ;; *) ..., the second is the `users ...` setup dispatch,
# both of which also have a "<group>)" line and would otherwise be matched first). A deliberately
# repeated call (e.g. search's two "search restores URL state" invocations) prints twice, not
# deduplicated.
ni_e2e_calls() {
  awk -v grp="$1" '
    /^case \$group in$/ { casenum++ }
    casenum == 3 && $0 ~ "^  " grp "\\)" { inblock = 1 }
    casenum == 3 && inblock { print }
    casenum == 3 && inblock && /;;/ { inblock = 0 }
  ' "$root/infra/test-e2e.sh" | grep -oE 'e2e "[^"]*"' | sed -e 's/^e2e "//' -e 's/"$//'
}

ni_stage build
$compose build backend-test frontend-test

ni_stage backend.full
$compose run --rm --no-deps backend-test pytest --collect-only -q tests \
  > "$artifacts/inventory/backend-full.txt"

ni_stage backend.quick
if [ -f "$root/backend/tests/classify.py" ]; then
  $compose run --rm --no-deps backend-test \
    sh -c 'pytest --collect-only -q $(python tests/classify.py --paths unit)' \
    > "$artifacts/inventory/backend-quick.txt"
else
  echo "backend/tests/classify.py absent; backend-quick.txt not written (pre-Task-3 tree)" >&2
fi

ni_stage frontend.tests
$compose run --rm --no-deps frontend-test npx vitest list > "$artifacts/inventory/frontend-tests.txt"

for group in search investigations monitors graph; do
  ni_stage "e2e.$group.invocations"
  ni_e2e_calls "$group" > "$artifacts/inventory/e2e-$group-invocations.txt"
  [ -s "$artifacts/inventory/e2e-$group-invocations.txt" ] || {
    echo "No e2e() calls found for $group group in infra/test-e2e.sh" >&2
    exit 1
  }

  ni_stage "e2e.$group.specs"
  filter=$(tr '\n' '|' < "$artifacts/inventory/e2e-$group-invocations.txt" | sed 's/|$//')
  $compose run --rm --no-deps frontend-test npx playwright test --list --reporter=line --grep "$filter" \
    > "$artifacts/inventory/e2e-$group-specs.txt"
done

echo "Inventory written: $artifacts/inventory"
