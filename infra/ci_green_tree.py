"""Finds a green full-gate run on a commit with the same tree as the one being tested.

A squash merge of an up-to-date branch gives main the exact tree the branch's full gate already
passed on, so full-gate.yml skips running it again. Usage:

    ci_green_tree.py <tree> <full-gate runs.json> <targeted runs.json>

Each JSON file is one page of GET /repos/{repo}/actions/workflows/{file}/runs. Prints the run's
URL and exits 0 when one matches, exits 1 when none does.
"""

import json
import sys
from typing import Any


def _asks_for_full(message: str) -> bool:
    # targeted.yml acts on the last `ci-run:` line of the commit message.
    lines = [line for line in message.splitlines() if line.startswith("ci-run:")]
    return bool(lines) and lines[-1].removeprefix("ci-run:").split() == ["full"]


def green_run(
    tree: str, full_runs: list[dict[str, Any]], targeted_runs: list[dict[str, Any]]
) -> str | None:
    # A dispatched full-gate run may have run one loop only, so only push, PR and nightly runs count.
    candidates = [run for run in full_runs if run["event"] != "workflow_dispatch"]
    candidates += [run for run in targeted_runs if _asks_for_full(run["head_commit"]["message"])]
    for run in candidates:
        if run["conclusion"] == "success" and run["head_commit"]["tree_id"] == tree:
            return str(run["html_url"])
    return None


def main(argv: list[str]) -> int:
    tree, full_path, targeted_path = argv
    pages = []
    for path in (full_path, targeted_path):
        with open(path) as page:
            pages.append(json.load(page)["workflow_runs"])
    url = green_run(tree, pages[0], pages[1])
    if url is None:
        return 1
    print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
