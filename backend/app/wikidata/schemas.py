from datetime import date, datetime
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
    # Open candidates while the root is unlinked, best first; a search queued by the button.
    candidates: list["WikidataCandidateResponse"]
    search_pending: bool
    # The root holding the item this one was merged into: a merge to offer.
    redirect_holder: "WikidataHolder | None"


class WikidataCandidateResponse(BaseModel):
    qid: str
    score: float
    # Stated reasons: label:<lang>, alias:<lang>, variant:<lang>, type_matches, type_differs,
    # sitelinks:<count>.
    reasons: list[str]
    # The root's one exact label of the right type: what "approve all exact" links.
    exact: bool
    label: str | None
    description: str | None
    sitelinks: int


class WikidataReviewItem(WikidataCandidateResponse):
    entity_id: str
    display_name: str
    entity_type: str
    language: str


class WikidataReviewPage(BaseModel):
    items: list[WikidataReviewItem]
    next_cursor: str | None


class WikidataRunResponse(BaseModel):
    id: str
    kind: str
    status: str
    entity_id: str | None
    checked: int
    changed: int
    redirected: int
    missing: int
    errors: int
    requests: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class WikidataSkipped(BaseModel):
    entity_id: str
    qid: str
    message: str


class WikidataApproveResponse(BaseModel):
    linked: int
    skipped: list[WikidataSkipped]


class WikidataHolder(BaseModel):
    entity_id: str
    display_name: str


class WikidataThrottleResponse(BaseModel):
    # open, paused (a 429/503, maxlag or an error asked us to wait) or budget_spent (today's).
    state: Literal["open", "paused", "budget_spent"]
    paused_until: datetime | None
    pause_reason: str | None
    requests_today: int
    daily_budget: int
    next_request_at: datetime | None


class WikidataCountResponse(BaseModel):
    day: date
    kind: str
    outcome: str
    count: int
    average_ms: int


class WikidataStatusResponse(BaseModel):
    enabled: bool
    # Why it is off: switched off, or no contact for the User-Agent.
    reason: str | None
    throttle: WikidataThrottleResponse
    counts: list[WikidataCountResponse]
    links: int
    open_candidates: int
    # Linked items whose monthly check is due.
    due_refresh: int
    runs: list[WikidataRunResponse]
    last_refresh: WikidataRunResponse | None


WikidataLinkResponse.model_rebuild()
