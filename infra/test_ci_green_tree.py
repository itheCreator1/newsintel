"""Tests for ci_green_tree.py; the quick gate runs them in the backend-test image."""

import json
import subprocess
import sys
from pathlib import Path

from ci_green_tree import green_run

SCRIPT = Path(__file__).with_name("ci_green_tree.py")


def _run(tree: str, message: str = "change", event: str = "pull_request") -> dict:
    return {
        "event": event,
        "conclusion": "success",
        "html_url": f"https://example.test/runs/{tree}",
        "head_commit": {"tree_id": tree, "message": message},
    }


def test_a_green_full_run_on_the_same_tree_is_found() -> None:
    assert green_run("t1", [_run("t0"), _run("t1")], []) == "https://example.test/runs/t1"


def test_a_different_tree_is_not_enough() -> None:
    assert green_run("t1", [_run("t0")], []) is None


def test_a_red_run_does_not_count() -> None:
    run = _run("t1") | {"conclusion": "failure"}
    assert green_run("t1", [run], []) is None


def test_a_dispatched_full_gate_run_may_have_run_one_loop_only() -> None:
    assert green_run("t1", [_run("t1", event="workflow_dispatch")], []) is None


def test_a_targeted_run_counts_only_when_its_last_trailer_asks_for_full() -> None:
    full = _run("t1", "fix\n\nci-run: e2e graph\nci-run: full", event="push")
    graph = _run("t1", "fix\n\nci-run: full\nci-run: e2e graph", event="push")
    assert green_run("t1", [], [graph]) is None
    assert green_run("t1", [], [full]) == "https://example.test/runs/t1"


def test_the_command_line_reads_the_api_pages(tmp_path: Path) -> None:
    full = tmp_path / "full.json"
    targeted = tmp_path / "targeted.json"
    full.write_text(json.dumps({"workflow_runs": [_run("t1")]}))
    targeted.write_text(json.dumps({"workflow_runs": []}))
    found = subprocess.run(
        [sys.executable, str(SCRIPT), "t1", str(full), str(targeted)],
        capture_output=True,
        text=True,
        check=False,
    )
    missing = subprocess.run(
        [sys.executable, str(SCRIPT), "t2", str(full), str(targeted)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (found.returncode, found.stdout.strip()) == (0, "https://example.test/runs/t1")
    assert missing.returncode == 1
