"""Why a Wikidata request was not sent, or failed. None of them is retried on the spot."""

from datetime import datetime


class WikidataError(Exception):
    pass


class WikidataDisabled(WikidataError):
    """Switched off, or no contact set: Wikimedia's User-Agent policy requires one."""


class WikidataPaused(WikidataError):
    """An earlier answer asked us to stop; nothing is sent before `until`."""

    def __init__(self, until: datetime, reason: str) -> None:
        super().__init__(f"Wikidata is paused until {until.isoformat()} ({reason})")
        self.until = until
        self.reason = reason


class WikidataBudgetSpent(WikidataError):
    """Today's request budget is used up; it starts again at midnight UTC."""

    def __init__(self, until: datetime) -> None:
        super().__init__(f"Today's Wikidata request budget is spent until {until.isoformat()}")
        self.until = until


class WikidataUnavailable(WikidataError):
    """The request went out and failed; the throttle has already recorded the pause."""

    def __init__(self, outcome: str, detail: str) -> None:
        super().__init__(f"Wikidata request failed ({outcome}): {detail}")
        self.outcome = outcome
        self.detail = detail
