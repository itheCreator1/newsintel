"""Ask the real Wikidata API a few questions and check that our parsers still read the answers.

The test suite never reaches the network; this runs from the "Wikidata contract" workflow (and
by hand: `python -m app.wikidata.contract`). It sends six requests through the same client and
throttle as the app, one every few seconds, and names anything that changed shape.
"""

import asyncio
import os
import sys
from dataclasses import dataclass, field

from app.core.config import Settings
from app.wikidata.client import WikidataClient
from app.wikidata.throttle import MemoryThrottle
from app.wikidata.types import TYPE_ROOTS

# Douglas Adams: a long-lived, well-kept item with all the identifiers we read.
PROBE = "Q42"
PROBE_LABEL = "Douglas Adams"
CLASS_ROOTS = tuple(qid for roots in TYPE_ROOTS.values() for qid in roots)


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def expect(self, ok: bool, problem: str) -> None:
        if not ok:
            self.problems.append(problem)


async def check(client: WikidataClient) -> Report:
    report = Report()
    found = await client.search(PROBE_LABEL, "en")
    report.expect(PROBE in found, f"wbsearchentities: {PROBE} not among {found}")

    pages = await client.info([PROBE])
    page = pages.get(PROBE)
    report.expect(page is not None and page.state == "ok", f"prop=info: {PROBE} not found")
    if page is not None:
        report.expect(isinstance(page.revision, int), "prop=info: no revision (lastrevid)")
        report.expect(isinstance(page.length, int), "prop=info: no size (length)")

    items = {item.qid: item for item in await client.items([PROBE], full=True)}
    adams = items.get(PROBE)
    report.expect(adams is not None and adams.state == "ok", f"wbgetentities: {PROBE} not read")
    if adams is not None:
        report.expect(adams.labels.get("en") == PROBE_LABEL, f"labels: {adams.labels}")
        report.expect(isinstance(adams.revision, int), "wbgetentities: no revision (lastrevid)")
        report.expect("Q5" in adams.instance_of, f"P31: {adams.instance_of}")
        for scheme in ("viaf", "isni", "lcnaf"):
            report.expect(scheme in adams.ids, f"identifiers: no {scheme} in {adams.ids}")
        report.expect(adams.sitelinks > 0, "sitelinks: none counted")
        report.expect(bool(adams.descriptions), "descriptions: none read")

    report.expect(
        "Q5" in await client.claim_items(PROBE, "P31"), "wbgetclaims: P31 does not name Q5"
    )

    classes = {item.qid: item for item in await client.items(CLASS_ROOTS)}
    for entity_type, roots in TYPE_ROOTS.items():
        for qid in roots:
            item = classes.get(qid)
            ok = item is not None and item.state == "ok"
            report.expect(ok, f"class root {qid} ({entity_type}) is missing or redirected")
            if ok and item is not None:
                report.notes.append(f"{entity_type}: {qid} = {item.labels.get('en')!r}")
    return report


async def _main() -> int:
    contact = os.getenv("NEWSINTEL_WIKIDATA_CONTACT", "")
    settings = Settings(wikidata_contact=contact)
    async with WikidataClient(settings, MemoryThrottle(settings)) as client:
        if not client.enabled:
            print("Set NEWSINTEL_WIKIDATA_CONTACT to an email or URL first.", file=sys.stderr)
            return 2
        report = await check(client)
    for note in report.notes:
        print(note)
    for problem in report.problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    print("contract ok" if not report.problems else f"{len(report.problems)} problem(s)")
    return 1 if report.problems else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
