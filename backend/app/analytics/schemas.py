import uuid
from datetime import date

from pydantic import BaseModel


class IngestionTimelineBucket(BaseModel):
    date: date
    count: int
    is_spike: bool


class IngestionTimelineResponse(BaseModel):
    buckets: list[IngestionTimelineBucket]


class TopEntity(BaseModel):
    entity_id: uuid.UUID
    display_text: str
    entity_type: str
    count: int


class TopEntitiesResponse(BaseModel):
    entities: list[TopEntity]


class TopCountry(BaseModel):
    country_code: str
    count: int


class TopCountriesResponse(BaseModel):
    countries: list[TopCountry]


class RisingEntity(BaseModel):
    entity_id: uuid.UUID
    display_text: str
    entity_type: str
    # Articles naming it in the last 24 hours, and per day over the 7 days before.
    today: int
    daily_average: float


class RisingEntitiesResponse(BaseModel):
    entities: list[RisingEntity]
