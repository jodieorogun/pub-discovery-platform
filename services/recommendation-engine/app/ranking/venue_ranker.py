"""Transparent weighted venue scoring and explanation generation."""

from dataclasses import dataclass
from math import asin, cos, radians, sin, sqrt

from app.models.venue import Venue
from app.preference_features import FEATURE_REASONS, featureValue
from app.schemas.recommendation import ParsedPreferences, VenueRecommendation


@dataclass(frozen=True)
class RankingWeights:
    """Tunable weights for every supported ranking factor."""

    location: float = 0.25
    price: float = 0.15
    features: float = 0.20
    groupSuitability: float = 0.10
    rating: float = 0.20
    popularity: float = 0.10


RANKING_WEIGHTS = RankingWeights()
RANKING_VERSION = "weighted-v3-five-features"
AREA_CENTRES: dict[str, tuple[float, float]] = {
    "Waterloo": (51.5033, -0.1147),
    "London Bridge": (51.5055, -0.0865),
    "Shoreditch": (51.5255, -0.0776),
    "Brixton": (51.4613, -0.1156),
    "Camden": (51.5390, -0.1426),
    "Westminster": (51.5010, -0.1300),
}


def rankVenues(
    venues: list[Venue],
    preferences: ParsedPreferences,
    limit: int,
    weights: RankingWeights = RANKING_WEIGHTS,
) -> list[VenueRecommendation]:
    """Score candidates using active preference factors and quality signals."""
    recommendations = [scoreVenue(venue, preferences, weights) for venue in venues]
    return sorted(
        recommendations,
        key=lambda recommendation: (-recommendation.score, recommendation.name),
    )[:limit]


def scoreVenue(
    venue: Venue,
    preferences: ParsedPreferences,
    weights: RankingWeights = RANKING_WEIGHTS,
) -> VenueRecommendation:
    """Calculate one normalised venue score and factual explanation list."""
    factors: list[tuple[str, float, float]] = []
    if venue.rating is not None:
        factors.append(("rating", weights.rating, venue.rating / 5.0))
    if venue.popularityScore is not None:
        factors.append(("popularity", weights.popularity, venue.popularityScore))
    reasons: list[str] = []

    distanceKm = calculateDistanceKm(venue, preferences.location)
    if preferences.location:
        locationScore = max(0.0, 1.0 - (distanceKm or 0.0) / 8.0)
        factors.append(("location", weights.location, locationScore))
        if venue.area == preferences.location:
            reasons.append(f"Located in {preferences.location}")
    if preferences.priceLevel and venue.priceLevel is not None:
        priceMatch = venue.priceLevel == preferences.priceLevel
        factors.append(("price", weights.price, float(priceMatch)))
        if priceMatch:
            reasons.append("Matches the requested price range")

    featureMatches: list[bool] = []
    for requested, available, reason in (
        (preferences.requiresFood, venue.servesFood, "Serves food"),
        (preferences.requiresOutdoorSeating, venue.hasOutdoorSeating, "Has outdoor seating"),
        (
            preferences.excludesOutdoorSeating,
            venue.hasOutdoorSeating is False,
            "Has no outdoor seating",
        ),
        (preferences.showsSports, venue.showsSports, "Shows sports"),
        (preferences.requiresLiveMusic, venue.hasLiveMusic, "Has live music"),
        (preferences.requiresDj, venue.hasDj, "Has DJ events"),
        (preferences.requiresEvents, venue.hostsEvents, "Hosts events"),
        (preferences.requiresDogFriendly, venue.dogFriendly, "Dog friendly"),
        (
            preferences.requiresWheelchairAccess,
            venue.wheelchairAccessible,
            "Has verified wheelchair access",
        ),
        (
            preferences.requiresReservations,
            venue.acceptsReservations,
            "Accepts table bookings",
        ),
        (preferences.requiresHappyHour, venue.hasHappyHour, "Has a happy hour"),
        (
            preferences.requiresSundayRoast,
            venue.servesSundayRoast,
            "Serves a Sunday roast",
        ),
        (
            preferences.requiresVeganOptions,
            venue.hasVeganOptions,
            "Has vegan or vegetarian options",
        ),
        (preferences.requiresQuizNight, venue.hostsQuiz, "Hosts a quiz night"),
        (
            preferences.requiresAccessibleToilet,
            venue.hasAccessibleToilet,
            "Has an accessible toilet",
        ),
        (
            preferences.noiseLevel is not None,
            (None if venue.noiseLevel is None else venue.noiseLevel == preferences.noiseLevel),
            f"Has a {preferences.noiseLevel} atmosphere",
        ),
    ):
        if requested and available is not None:
            featureMatches.append(available)
            if available is True:
                reasons.append(reason)
    if featureMatches:
        factors.append(
            ("requiredFeatures", weights.features, sum(featureMatches) / len(featureMatches))
        )

    if preferences.preferredFeatures:
        knownPreferences = [
            (feature, value)
            for feature in preferences.preferredFeatures
            if (value := featureValue(venue, feature)) is not None
        ]
        if knownPreferences:
            factors.append(
                (
                    "preferredFeatures",
                    weights.features,
                    sum(value for _, value in knownPreferences) / len(knownPreferences),
                )
            )
            for feature, matched in knownPreferences:
                if matched:
                    reasons.append(FEATURE_REASONS.get(feature, f"Matches preferred {feature}"))

    if preferences.suitableForGroups and venue.suitableForGroups is not None:
        factors.append(
            (
                "groupSuitability",
                weights.groupSuitability,
                float(venue.suitableForGroups is True),
            )
        )
        if venue.suitableForGroups is True:
            reasons.append("Supports group bookings or private hire")

    totalWeight = sum(weight for _, weight, _ in factors)
    score = (
        sum(weight * value for _, weight, value in factors) / totalWeight if totalWeight else 0.0
    )
    # Contributions are normalised, so their sum is the final deterministic score.
    scoreBreakdown = {
        name: round(weight * value / totalWeight, 3)
        for name, weight, value in factors
        if totalWeight
    }
    if venue.rating is not None:
        reasons.append(f"Rated {venue.rating:.1f} out of 5")
    if venue.popularityScore is not None and venue.popularityScore >= 0.8:
        reasons.append("Strong local popularity")
    return VenueRecommendation(
        venueId=venue.venueId,
        name=venue.name,
        latitude=venue.latitude,
        longitude=venue.longitude,
        address=venue.address,
        postcode=venue.postcode,
        website=venue.website,
        phone=venue.phone,
        priceTier=venue.priceTier,
        priceLevel=venue.priceLevel,
        guinnessPriceGbp=venue.guinnessPriceGbp,
        priceEvidenceUrl=venue.priceEvidenceUrl,
        score=round(min(1.0, max(0.0, score)), 3),
        distanceKm=distanceKm,
        reasons=reasons,
        scoreBreakdown=scoreBreakdown,
        # These are facts with an explicit true value, not inferred amenities.
        verifiedFeatures=[
            reason
            for feature, reason in FEATURE_REASONS.items()
            if featureValue(venue, feature) is True
        ],
        unknownFeatures=[
            reason
            for feature, reason in FEATURE_REASONS.items()
            if featureValue(venue, feature) is None
        ],
    )


def calculateDistanceKm(venue: Venue, location: str | None) -> float | None:
    """Calculate distance from a venue to the requested area's centre."""
    if location is None:
        return None
    centreLatitude, centreLongitude = AREA_CENTRES[location]
    latitudeDelta = radians(venue.latitude - centreLatitude)
    longitudeDelta = radians(venue.longitude - centreLongitude)
    startLatitude = radians(centreLatitude)
    endLatitude = radians(venue.latitude)
    haversineValue = sin(latitudeDelta / 2) ** 2 + (
        cos(startLatitude) * cos(endLatitude) * sin(longitudeDelta / 2) ** 2
    )
    return round(6371.0 * 2 * asin(sqrt(haversineValue)), 2)
