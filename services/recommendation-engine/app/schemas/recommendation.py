"""Pydantic schemas for recommendation requests and responses."""

from pydantic import BaseModel, Field, field_validator


class RecommendationRequest(BaseModel):
    """A natural-language recommendation request."""

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)
    offset: int = Field(default=0, ge=0)

    @field_validator("query")
    @classmethod
    def rejectBlankQuery(cls, value: str) -> str:
        """Reject strings containing only whitespace."""
        if not value.strip():
            raise ValueError("query must not be empty")
        return value.strip()


class ParsedPreferences(BaseModel):
    """Structured preferences extracted from a natural-language query."""

    location: str | None = None
    excludedLocations: list[str] = Field(default_factory=list)
    priceLevel: str | None = None
    excludedPriceLevels: list[str] = Field(default_factory=list)
    requiresFood: bool = False
    requiresOutdoorSeating: bool = False
    excludesOutdoorSeating: bool = False
    showsSports: bool = False
    requiresLiveMusic: bool = False
    requiresDj: bool = False
    requiresEvents: bool = False
    requiresDogFriendly: bool = False
    requiresWheelchairAccess: bool = False
    requiresReservations: bool = False
    suitableForGroups: bool = False
    requiresHappyHour: bool = False
    requiresSundayRoast: bool = False
    requiresVeganOptions: bool = False
    requiresQuizNight: bool = False
    requiresAccessibleToilet: bool = False
    groupSize: int | None = Field(default=None, ge=1, le=50)
    noiseLevel: str | None = None
    preferredFeatures: list[str] = Field(default_factory=list)
    excludedFeatures: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    unparsedTerms: list[str] = Field(default_factory=list)


class VenueRecommendation(BaseModel):
    """A ranked venue with a transparent score and reasons."""

    venueId: str
    name: str
    latitude: float
    longitude: float
    address: str | None = None
    postcode: str | None = None
    website: str | None = None
    phone: str | None = None
    priceTier: int | None = Field(default=None, ge=1, le=4)
    priceLevel: str | None = None
    guinnessPriceGbp: float | None = Field(default=None, gt=0, le=30)
    priceEvidenceUrl: str | None = None
    score: float = Field(ge=0.0, le=1.0)
    distanceKm: float | None = Field(default=None, ge=0.0)
    reasons: list[str]
    scoreBreakdown: dict[str, float] = Field(default_factory=dict)
    semanticScore: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    evidenceSources: list[str] = Field(default_factory=list)
    evidenceSourceUrls: list[str] = Field(default_factory=list)
    verifiedFeatures: list[str] = Field(default_factory=list)
    unknownFeatures: list[str] = Field(default_factory=list)
    beenHere: bool = False
    userRating: int | None = Field(default=None, ge=1, le=5)
    personalScore: float | None = Field(default=None, ge=0.0, le=1.0)
    personalReason: str | None = None


class RecommendationResponse(BaseModel):
    """Recommendation endpoint response."""

    query: str
    parsedPreferences: ParsedPreferences
    recommendations: list[VenueRecommendation]
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
    totalAvailable: int = Field(ge=0)
    hasMore: bool = False
    retrievalMode: str = "deterministic"
    requestId: str
    parserVersion: str
    rankingVersion: str
    datasetVersion: str
    indexVersion: str | None = None
    activePreferences: list[str] = Field(default_factory=list)
    ignoredPreferences: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    noResultReasons: list[str] = Field(default_factory=list)
    personalised: bool = False
