from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

QID_PATTERN = r"^Q[1-9][0-9]*$"


class WikidataName(BaseModel):
    language: str = Field(max_length=16)
    text: str = Field(min_length=1, max_length=500)


class WikidataLinkRequest(BaseModel):
    qid: str = Field(pattern=QID_PATTERN, max_length=16)
    # The item's aliases the user ticked; its labels are always added.
    aliases: list[WikidataName] = Field(default_factory=list, max_length=200)


class WikidataNamesRequest(BaseModel):
    names: list[WikidataName] = Field(min_length=1, max_length=200)


class WikidataNameResponse(BaseModel):
    language: str
    text: str
    kind: Literal["label", "alias"]
    # this_entity: already one of its names; other_entity: a name of another entity (see the
    # "Maybe the same?" queue); absent: no entity has it yet.
    status: Literal["this_entity", "other_entity", "absent"]
    entity_id: str | None


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
    names: list[WikidataNameResponse]
