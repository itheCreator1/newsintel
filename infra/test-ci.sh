#!/bin/sh
# CI entry point shared by .github/workflows/full-gate.yml and targeted.yml: runs one loop by
# name, so a run that is not the whole gate takes minutes instead of the gate's quarter hour.
# Usage: infra/test-ci.sh full
#        infra/test-ci.sh e2e <group> [<spec>]     <spec> is passed to test-e2e.sh --stop-after
#        infra/test-ci.sh integration <pytest-node-id>...
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
usage="usage: infra/test-ci.sh full | e2e <group> [<spec>] | integration <pytest-node-id>..."
run=${1:?$usage}
shift

case $run in
  full)
    [ "$#" -eq 0 ] || { echo "$usage" >&2; exit 2; }
    exec "$root/infra/test-docker.sh"
    ;;
  e2e)
    [ "$#" -ge 1 ] && [ "$#" -le 2 ] || { echo "$usage" >&2; exit 2; }
    if [ -n "${2:-}" ]; then
      exec "$root/infra/test-e2e.sh" --stop-after "$2" "$1"
    fi
    exec "$root/infra/test-e2e.sh" "$1"
    ;;
  integration)
    [ "$#" -ge 1 ] || { echo "$usage" >&2; exit 2; }
    exec "$root/infra/test-integration.sh" "$@"
    ;;
  *)
    echo "Unknown run: $run" >&2
    echo "$usage" >&2
    exit 2
    ;;
esac
