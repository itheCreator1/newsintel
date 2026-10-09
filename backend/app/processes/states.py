"""How a card's numbers become its state; the same rules for every process of a kind."""

from app.processes.schemas import ProcessState


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
