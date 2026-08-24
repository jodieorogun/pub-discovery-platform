"""Validated account and personal rating API payloads."""

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    displayName: str = Field(min_length=1, max_length=50)

    @field_validator("displayName")
    @classmethod
    def trimName(cls, value: str) -> str:
        return value.strip()

    @field_validator("email")
    @classmethod
    def normaliseEmail(cls, value: str) -> str:
        email = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(email):
            raise ValueError("enter a valid email address")
        return email


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normaliseEmail(cls, value: str) -> str:
        email = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(email):
            raise ValueError("enter a valid email address")
        return email


class RatingRequest(BaseModel):
    """A null rating records a visit without forcing a star score."""

    beenHere: bool = True
    rating: float | None = Field(default=None, ge=1, le=5, multiple_of=0.5)
    privateNote: str | None = Field(default=None, max_length=2000)
    photoDataUrl: str | None = Field(default=None, max_length=7_000_000)

    @field_validator("privateNote")
    @classmethod
    def trimPrivateNote(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class DiaryEntry(BaseModel):
    venueId: str
    name: str
    area: str
    address: str | None = None
    postcode: str | None = None
    rating: float | None = Field(default=None, ge=1, le=5, multiple_of=0.5)
    privateNote: str | None = None
    photoUrl: str | None = None
    visitedAt: datetime
    updatedAt: datetime


class DiaryStats(BaseModel):
    visitedTotal: int = Field(ge=0)
    visitedThisWeek: int = Field(ge=0)
    ratedTotal: int = Field(ge=0)
    averageRating: float | None = Field(default=None, ge=1, le=5)
    londonVisited: int = Field(ge=0)
    londonTotal: int = Field(ge=0)
    londonPercent: float = Field(ge=0, le=100)
