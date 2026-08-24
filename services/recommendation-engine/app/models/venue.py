"""Venue domain model."""

from datetime import date

from pydantic import BaseModel, Field

from app.models.provenance import AttributeProvenance


class Venue(BaseModel):
    """A venue available to the recommendation engine."""

    venueId: str
    name: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    area: str
    address: str | None = None
    postcode: str | None = None
    website: str | None = None
    phone: str | None = None
    priceTier: int | None = Field(default=None, ge=1, le=4)
    priceLevel: str | None = None
    guinnessPriceGbp: float | None = Field(default=None, gt=0, le=30)
    priceEvidenceUrl: str | None = None
    servesFood: bool | None = None
    hasOutdoorSeating: bool | None = None
    showsSports: bool | None = None
    hasLiveMusic: bool | None = None
    hasDj: bool | None = None
    hostsEvents: bool | None = None
    dogFriendly: bool | None = None
    wheelchairAccessible: bool | None = None
    acceptsReservations: bool | None = None
    suitableForGroups: bool | None = None
    hasHappyHour: bool | None = None
    servesSundayRoast: bool | None = None
    hasVeganOptions: bool | None = None
    hostsQuiz: bool | None = None
    hasAccessibleToilet: bool | None = None
    noiseLevel: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    popularityScore: float | None = Field(default=None, ge=0, le=1)
    tags: list[str]
    dataSource: str = "unknown"
    sourceUrl: str | None = None
    lastVerifiedDate: date | None = None
    attributeProvenance: dict[str, AttributeProvenance] = Field(default_factory=dict)
