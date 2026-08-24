"""Local account and pub-rating domain models."""

from datetime import datetime

from pydantic import BaseModel, Field


class UserAccount(BaseModel):
    """A small public account record; password data never leaves the repository."""

    userId: str
    email: str
    displayName: str
    createdAt: datetime


class VenueRating(BaseModel):
    """A user's latest visit and optional one-to-five star score for a pub."""

    userId: str
    venueId: str
    beenHere: bool = True
    rating: float | None = Field(default=None, ge=1, le=5, multiple_of=0.5)
    privateNote: str | None = None
    hasPhoto: bool = False
    visitedAt: datetime
    updatedAt: datetime
