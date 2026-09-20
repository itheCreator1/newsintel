import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.entities.schemas import EntityClusterResponse
from app.feeds.schemas import ArticleResponse

Kind = Literal["entity", "source", "country"]
# `story` and `mentioned` are the country annotation roles (`primary` and `mentioned`); `source`
# means the feed's own country. A comparison uses exactly one role.
Role = Literal["story", "mentioned", "source"]
Part = Literal["a", "both", "b"]


class Subject(BaseModel):
    kind: Kind
    ref: str
    label: str
    role: Role | None
    retired: bool


class CompareDay(BaseModel):
    date: date
    article_count: int


class CompareSide(BaseModel):
    """Counts are over the subject's articles dated in the window. `sources` is null when the
    subject is itself a source."""

    subject: Subject
    articles: int
    stories: int
    sources: int | None
    timeline: list[CompareDay]


class Overlap(BaseModel):
    """Two sets, A and B. `jaccard` is `both / union` and null when the union is empty."""

    only_a: int
    both: int
    only_b: int
    union: int
    jaccard: float | None


class CompareOverlap(BaseModel):
    """Articles are distinct articles; stories are clusters holding an article of the subject; a
    story is `both` when it holds one of each. `sources` (distinct feeds) is null for sources."""

    articles: Overlap
    stories: Overlap
    sources: Overlap | None


class RelatedItem(BaseModel):
    id: uuid.UUID
    label: str
    a_articles: int
    b_articles: int


class RelatedCountry(BaseModel):
    country_code: str
    role: str
    a_articles: int
    b_articles: int


class CompareRelated(BaseModel):
    """Top items over the union of both article sets, ranked by `a_articles + b_articles`. Zero
    means absent from that side. The compared subjects are left out."""

    entities: list[RelatedItem]
    countries: list[RelatedCountry]
    sources: list[RelatedItem] | None


class CompareResponse(BaseModel):
    kind: Kind
    role: Role | None
    window_days: int
    a: CompareSide
    b: CompareSide
    overlap: CompareOverlap
    related: CompareRelated


class CompareArticlePage(BaseModel):
    items: list[ArticleResponse]
    next_cursor: str | None


class CompareClusterResponse(EntityClusterResponse):
    """Subject articles in the window that the story holds."""

    a_articles: int
    b_articles: int


class CompareClusterPage(BaseModel):
    items: list[CompareClusterResponse]
    next_cursor: str | None
