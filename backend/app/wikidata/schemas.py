from datetime import datetime

from pydantic import BaseModel, Field

QID_PATTERN = r"^Q[1-9][0-9]*$"


class WikidataLinkRequest(BaseModel):
    qid: str = Field(pattern=QID_PATTERN, max_length=16)


class WikidataItemResponse(BaseModel):
    qid: str
    state: str
    redirect_to: str | None
    revision: int | None
    labels: dict[str, str]
    aliases: dict[str, list[str]]
    descriptions: dict[str, str]
    instance_of: list[str]
    different_from: list[str]
    sitelinks: int
    claims_fetched: bool
    fetched_at: datetime
    checked_at: datetime


class WikidataLinkResponse(BaseModel):
    entity_id: str
    qid: str | None
    # VIAF, ISNI and LCNAF read off the item.
    identifiers: dict[str, str]
    item: WikidataItemResponse | None
    # The linked item is waiting for its full fetch (claims and identifiers).
    fetch_pending: bool
