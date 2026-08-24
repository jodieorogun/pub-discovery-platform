"""Domain models for recommendation feedback events."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class FeedbackEventType(StrEnum):
    """Supported user interactions with a recommendation."""

    impression = "impression"
    click = "click"
    save = "save"
    like = "like"
    dislike = "dislike"


class FeedbackEvent(BaseModel):
    """One immutable, anonymous recommendation interaction."""

    eventId: str = Field(min_length=1)
    venueId: str = Field(min_length=1)
    eventType: FeedbackEventType
    query: str | None = None
    recommendationRank: int | None = Field(default=None, ge=1, le=20)
    sessionId: str | None = None
    requestId: str | None = None
    rankingVersion: str | None = None
    datasetVersion: str | None = None
    recordedAt: datetime
