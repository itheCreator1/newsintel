"""Validates classification.toml against the actual backend/tests/test_*.py modules.

This validation *is* a pytest test (it classifies itself as "unit" below), not a conftest hook
or a standalone script, so it runs automatically in both test-quick.sh's unit selection and the
full Docker gate: an unclassified new test module fails immediately instead of silently entering
"quick" (unit-only, no services) execution.

Two of the marker strings this file greps for (the Postgres skip gate and the fixture-server
port var) are split across adjacent string literals below so this file's own source never
contains the literal marker substring -- otherwise the per-file checks below would flag this
file against itself (its own manifest entry has postgres=false, fixture_server=false).
"""

import tomllib
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = TESTS_DIR / "classification.toml"

# Split so this file's own source text never contains the literal marker (see module docstring).
POSTGRES_MARKER = "NEWSINTEL_RUN_POSTGRES" + "_TESTS"
FIXTURE_MARKER = "NEWSINTEL_TEST_FIXTURE" + "_PORT"


def _manifest() -> dict:
    with MANIFEST_PATH.open("rb") as f:
        return tomllib.load(f)


def _source(module_name: str) -> str:
    return (TESTS_DIR / module_name).read_text()


def test_manifest_matches_the_test_modules_on_disk() -> None:
    manifest_keys = set(_manifest())
    on_disk = {p.name for p in TESTS_DIR.glob("test_*.py")}
    missing_from_manifest = on_disk - manifest_keys
    stale_in_manifest = manifest_keys - on_disk
    assert not missing_from_manifest and not stale_in_manifest, (
        "classification.toml is out of sync with backend/tests/test_*.py -- "
        f"missing from manifest: {sorted(missing_from_manifest)}; "
        f"stale entries no longer on disk: {sorted(stale_in_manifest)}"
    )


def test_postgres_flag_matches_the_skip_gate_marker() -> None:
    mismatches = sorted(
        name
        for name, entry in _manifest().items()
        if (POSTGRES_MARKER in _source(name)) != entry["postgres"]
    )
    assert not mismatches, (
        f"postgres flag disagrees with the {POSTGRES_MARKER} source marker: {mismatches}"
    )


def test_unit_modules_declare_no_service_flags() -> None:
    violations = sorted(
        name
        for name, entry in _manifest().items()
        if entry["kind"] == "unit"
        and (entry["postgres"] or entry["elasticsearch"] or entry["fixture_server"])
    )
    assert not violations, f"unit-kind modules must not set any service flag: {violations}"


def test_elasticsearch_and_fixture_server_imply_postgres() -> None:
    violations = sorted(
        name
        for name, entry in _manifest().items()
        if (entry["elasticsearch"] or entry["fixture_server"]) and not entry["postgres"]
    )
    assert not violations, (
        f"elasticsearch/fixture_server=true requires postgres=true: {violations}"
    )


def test_fixture_server_flag_matches_the_fixture_port_marker() -> None:
    mismatches = sorted(
        name
        for name, entry in _manifest().items()
        if (FIXTURE_MARKER in _source(name)) != entry["fixture_server"]
    )
    assert not mismatches, (
        f"fixture_server flag disagrees with the {FIXTURE_MARKER} source marker: {mismatches}"
    )


def test_elasticsearch_true_modules_mention_elasticsearch() -> None:
    # One direction only: elasticsearch=true modules must mention Elasticsearch in source, but
    # the reverse isn't checked -- some postgres-gated modules mention it while testing a
    # monkeypatched exception or a graceful-degradation route (test_operations_postgres.py)
    # without needing a live cluster. That direction is human-audited (see classification.toml's
    # per-entry `note`) and backstopped at runtime: test-integration.sh points
    # NEWSINTEL_ELASTICSEARCH_URL at http://127.0.0.1:1 whenever no selected module declares
    # elasticsearch, so a wrongly-false flag fails loudly instead of silently passing.
    violations = sorted(
        name
        for name, entry in _manifest().items()
        if entry["elasticsearch"] and "elasticsearch" not in _source(name).lower()
    )
    assert not violations, f"elasticsearch=true modules must mention Elasticsearch: {violations}"
