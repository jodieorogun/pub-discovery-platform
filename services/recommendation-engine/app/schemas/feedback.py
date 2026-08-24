"""API schemas for recommendation feedback."""

from pydantic import BaseModel, Field, field_validator

from app.models.feedback import FeedbackEventType


class FeedbackRequest(BaseModel):
    """A validated interaction with one recommended venue."""

    venueId: str = Field(min_length=1, max_length=100)
    eventType: FeedbackEventType
    query: str | None = Field(default=None, max_length=500)
    recommendationRank: int | None = Field(default=None, ge=1, le=20)
    sessionId: str | None = Field(default=None, max_length=100)
    requestId: str | None = Field(default=None, max_length=100)
    rankingVersion: str | None = Field(default=None, max_length=100)
    datasetVersion: str | None = Field(default=None, max_length=100)

    @field_validator(
        "venueId", "query", "sessionId", "requestId", "rankingVersion", "datasetVersion"
    )
    @classmethod
    def trimOptionalText(cls, value: str | None) -> str | None:
        """Trim text and reject supplied blank values."""
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("value must not be blank")
        return trimmed
