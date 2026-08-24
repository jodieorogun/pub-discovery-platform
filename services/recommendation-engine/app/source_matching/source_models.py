"""Normalised source records and source-link output models."""

from enum import StrEnum

from pydantic import BaseModel, Field


class SourcePlaceRecord(BaseModel):
    """Fields shared by normalised external venue records."""

    name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    address: str | None = None
    postcode: str | None = None
    website: str | None = None
    phone: str | None = None
    categories: list[str] = Field(default_factory=list)


class FsqPlaceRecord(SourcePlaceRecord):
    """A normalised FSQ place record."""

    fsqPlaceId: str = Field(min_length=1)
    priceTier: int | None = Field(default=None, ge=1, le=4)


class OsmPlaceRecord(SourcePlaceRecord):
    """A normalised OpenStreetMap place record."""

    osmElementId: str = Field(min_length=1)
    servesFood: bool | None = None
    hasOutdoorSeating: bool | None = None
    hasLiveMusic: bool | None = None
    dogFriendly: bool | None = None
    wheelchairAccessible: bool | None = None
    acceptsReservations: bool | None = None


class MatchStatus(StrEnum):
    """Confidence classification for a proposed source link."""

    definite = "definite"
    likely = "likely"
    review = "review"
    noMatch = "noMatch"


class PlaceLink(BaseModel):
    """A traceable proposed or accepted FSQ-to-OSM source link."""

    fsqPlaceId: str
    osmElementId: str
    matchScore: float = Field(ge=0, le=1)
    matchStatus: MatchStatus
    distanceMetres: float = Field(ge=0)
    signals: list[str]


class SourceMatchReport(BaseModel):
    """Accepted links, review candidates, and unmatched records."""

    acceptedLinks: list[PlaceLink] = Field(default_factory=list)
    manualReviewCandidates: list[PlaceLink] = Field(default_factory=list)
    unmatchedFsqPlaceIds: list[str] = Field(default_factory=list)
    unmatchedOsmElementIds: list[str] = Field(default_factory=list)
