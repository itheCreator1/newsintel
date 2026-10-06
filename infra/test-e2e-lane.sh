#!/bin/sh
# One lane of the full gate's concurrent browser stage. Not meant to be run by hand:
# infra/test-docker.sh starts each lane in its own session (setsid) so it can stop the lane's
# whole process tree with one signal.
# Usage: infra/test-e2e-lane.sh <manifest> <artifacts> <group>...
# Runs the groups in order through infra/test-e2e.sh --reuse-images, each with its output in
# <artifacts>/e2e-<group>.log, and appends each group's e2e.<group> row to <artifacts>/timings.tsv
# itself (ni_stage can't time a stage that overlaps the gate's own). Stops at the lane's first
# failure, as the sequential loop does.
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
usage="usage: infra/test-e2e-lane.sh <manifest> <artifacts> <group>..."
manifest=${1:?$usage}
artifacts=${2:?$usage}
shift 2
[ "$#" -gt 0 ] || { echo "$usage" >&2; exit 2; }

# The gate's TERM reaches the running group as well (same process group). That group tears its
# own Compose project down, and this trap only runs once it has exited -- so the lane neither
# returns before that cleanup finishes nor starts its next group. A TERM that lands between two
# groups reaches no group at all, hence the check right before each one starts.
stopped=0
trap 'stopped=1' TERM

for group in "$@"; do
  start=$(date +%s)
  status=0
  [ "$stopped" = 0 ] || exit 143
  NEWSINTEL_E2E_ARTIFACTS="$artifacts/e2e-$group" "$root/infra/test-e2e.sh" --reuse-images "$manifest" "$group" \
    > "$artifacts/e2e-$group.log" 2>&1 || status=$?
  # A group interrupted between two of its own commands can still exit 0; it did not pass.
  if [ "$stopped" = 1 ] && [ "$status" -eq 0 ]; then
    status=143
  fi
  printf 'e2e.%s\t%s\t%s\t%s\n' "$group" "$start" "$(($(date +%s) - start))" "$status" >> "$artifacts/timings.tsv"
  [ "$status" -eq 0 ] || exit "$status"
done
