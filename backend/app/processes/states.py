"""How a card's numbers become its state; the same rules for every process of a kind."""

import uuid

from app.processes.schemas import ProcessAction, ProcessKey, ProcessState

# Per-item processes whose failed rows can be tried again.
RETRYABLE: tuple[ProcessKey, ...] = (
    "feeds", "articles", "nlp", "clustering", "search", "monitors",
)  # fmt: skip
# Scheduled processes that can be run by hand.
RUNNABLE: tuple[ProcessKey, ...] = (
    "events", "retention", "wikidata_refresh", "wikidata_candidates",
)  # fmt: skip
# Runs that can be stopped part way; an authority run or a rebuild must finish once started.
STOPPABLE: tuple[ProcessKey, ...] = ("reprocessing", "wikidata_refresh", "wikidata_candidates")


def per_item(
    *, queued: int, running: int, retrying: int, failed: int, lease_expired: int,
    failed_in_window: int,
) -> ProcessState:  # fmt: skip
    """A queue of items. Only failures inside the window make it failing: an old failure stays
    listed (and retryable) without colouring the card for ever."""
    if failed_in_window:
        return "failing"
    if lease_expired:
        return "stalled"
    if retrying:
        return "retrying"
    if queued or running:
        return "working"
    return "ok"


def run(*, active: bool, last_failed: bool, ran: bool) -> ProcessState:
    """Runs one after another: the active one, else how the newest one ended."""
    if active:
        return "working"
    if last_failed:
        return "failing"
    return "ok" if ran else "idle"


def offers(
    key: ProcessKey, *, state: ProcessState, failed: int | None, active_run_id: uuid.UUID | None
) -> list[ProcessAction]:
    """What a card offers. A process with a run in hand offers to stop it, not to run again."""
    found: list[ProcessAction] = []
    if key in RETRYABLE and failed:
        found.append("retry_failed")
    if key in STOPPABLE and active_run_id is not None:
        found.append("stop")
    elif key in RUNNABLE and state != "off":
        found.append("run_now")
    return found


def can_retry(process: ProcessKey, status: str) -> bool:
    return process in RETRYABLE and status == "failed"


def can_stop(process: ProcessKey, status: str) -> bool:
    return process in STOPPABLE and status in ("queued", "running")
