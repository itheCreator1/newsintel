from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.feeds.schemas import ArticleResponse

# `story` and `mentioned` are the country annotation roles (`primary` and `mentioned`), `source` is
# the feed's own country and `event` is an event's `primary_country`, which is itself derived from
# the story role. A map shows exactly one role; roles are never added together.
Role = Literal["story", "mentioned", "source", "event"]
ArticleRole = Literal["story", "mentioned", "source"]


class GeoCountry(BaseModel):
    """Counts for one country in one role. An article with several countries counts once in each.
    `articles` and `stories` are null for the `event` role, `events` is null for every other role,
    and `sources` (distinct feeds) is only set for the `source` role."""

    country_code: str
    articles: int | None
    stories: int | None
    sources: int | None
    events: int | None


class GeoCoverage(BaseModel):
    """The base of every count: `window_total` items (articles, or events) in the window, of which
    `located` have a country in this role."""

    unit: Literal["articles", "events"]
    window_total: int
    located: int


class GeoCountriesResponse(BaseModel):
    role: Role
    days: int
    window_start: datetime
    coverage: GeoCoverage
    items: list[GeoCountry]


class GeoArticlePage(BaseModel):
    items: list[ArticleResponse]
    next_cursor: str | None
