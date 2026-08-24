"""Shared mappings for canonical recommendation features."""

from app.models.venue import Venue

# Keep parser-facing feature names stable even if the venue model changes later.
FEATURE_ATTRIBUTES: dict[str, str] = {
    "food": "servesFood",
    "outdoorSeating": "hasOutdoorSeating",
    "sports": "showsSports",
    "liveMusic": "hasLiveMusic",
    "dj": "hasDj",
    "events": "hostsEvents",
    "dogFriendly": "dogFriendly",
    "wheelchairAccess": "wheelchairAccessible",
    "reservations": "acceptsReservations",
    "groups": "suitableForGroups",
    "happyHour": "hasHappyHour",
    "sundayRoast": "servesSundayRoast",
    "veganOptions": "hasVeganOptions",
    "quizNight": "hostsQuiz",
    "accessibleToilet": "hasAccessibleToilet",
}

FEATURE_REASONS: dict[str, str] = {
    "food": "Serves food",
    "outdoorSeating": "Has outdoor seating",
    "sports": "Shows sports",
    "liveMusic": "Has live music",
    "dj": "Has DJ events",
    "events": "Hosts events",
    "dogFriendly": "Dog friendly",
    "wheelchairAccess": "Has verified wheelchair access",
    "reservations": "Accepts table bookings",
    "groups": "Supports group bookings or private hire",
    "happyHour": "Has a happy hour",
    "sundayRoast": "Serves a Sunday roast",
    "veganOptions": "Has vegan or vegetarian options",
    "quizNight": "Hosts a quiz night",
    "accessibleToilet": "Has an accessible toilet",
}


def featureValue(venue: Venue, feature: str) -> bool | None:
    """Return a verified feature value, including atmosphere aliases."""
    if feature == "quietAtmosphere":
        return None if venue.noiseLevel is None else venue.noiseLevel == "quiet"
    if feature == "livelyAtmosphere":
        return None if venue.noiseLevel is None else venue.noiseLevel == "lively"
    attribute = FEATURE_ATTRIBUTES.get(feature)
    return getattr(venue, attribute) if attribute else None
