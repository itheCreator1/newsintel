#!/usr/bin/env python3
"""Reads classification.toml on behalf of test-quick.sh and test-integration.sh.

Not a test module (not named test_*.py, never collected by pytest).

Usage:
  classify.py --paths unit                 space-separated pytest paths for every unit module
  classify.py --services <node-id>...      union of required services: postgres elasticsearch
                                            fixture_server (space-separated, only those needed)
  classify.py --kinds <node-id>...         "<module>\\t<kind>" per selected module, one per line
"""

import sys
import tomllib
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent / "classification.toml"


def load_manifest() -> dict:
    with MANIFEST.open("rb") as f:
        return tomllib.load(f)


def resolve(manifest: dict, node_id: str) -> tuple[str, dict]:
    """Normalize a pytest node id to its manifest key: strip a leading 'backend/', take
    everything left of the first '::', then the bare filename."""
    path_part = node_id.split("::", 1)[0]
    if path_part.startswith("backend/"):
        path_part = path_part[len("backend/") :]
    key = Path(path_part).name
    entry = manifest.get(key)
    if entry is None:
        sys.exit(f"classify.py: unrecognized test path {node_id!r} (module key {key!r})")
    return key, entry


def cmd_paths(manifest: dict, args: list[str]) -> None:
    if args != ["unit"]:
        sys.exit("classify.py --paths only supports 'unit'")
    paths = sorted(f"tests/{key}" for key, entry in manifest.items() if entry["kind"] == "unit")
    print(" ".join(paths))


def cmd_services(manifest: dict, node_ids: list[str]) -> None:
    if not node_ids:
        sys.exit("classify.py --services requires at least one node id")
    needed = {"postgres": False, "elasticsearch": False, "fixture_server": False}
    for node_id in node_ids:
        _, entry = resolve(manifest, node_id)
        for service in needed:
            needed[service] = needed[service] or entry[service]
    print(" ".join(service for service, on in needed.items() if on))


def cmd_kinds(manifest: dict, node_ids: list[str]) -> None:
    if not node_ids:
        sys.exit("classify.py --kinds requires at least one node id")
    for node_id in node_ids:
        key, entry = resolve(manifest, node_id)
        print(f"{key}\t{entry['kind']}")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    manifest = load_manifest()
    cmd, rest = args[0], args[1:]
    if cmd == "--paths":
        cmd_paths(manifest, rest)
    elif cmd == "--services":
        cmd_services(manifest, rest)
    elif cmd == "--kinds":
        cmd_kinds(manifest, rest)
    else:
        sys.exit(f"classify.py: unknown subcommand {cmd!r}")


if __name__ == "__main__":
    main()
