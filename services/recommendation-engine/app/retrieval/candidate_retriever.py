"""Hard-filter candidate venues from parsed preferences."""

from app.models.venue import Venue
from app.preference_features import featureValue
from app.schemas.recommendation import ParsedPreferences


def retrieveCandidates(venues: list[Venue], preferences: ParsedPreferences) -> list[Venue]:
    """Apply location and explicitly required feature filters."""
    if preferences.contradictions:
        return []
    return [
        venue
        for venue in venues
        if (preferences.location is None or venue.area == preferences.location)
        and venue.area not in preferences.excludedLocations
        and (
            not preferences.excludedPriceLevels
            or (
                venue.priceLevel is not None
                and venue.priceLevel not in preferences.excludedPriceLevels
            )
        )
        and (not preferences.requiresFood or venue.servesFood is True)
        and (not preferences.requiresOutdoorSeating or venue.hasOutdoorSeating is True)
        and (not preferences.excludesOutdoorSeating or venue.hasOutdoorSeating is False)
        and (not preferences.showsSports or venue.showsSports is True)
        and (not preferences.requiresLiveMusic or venue.hasLiveMusic is True)
        and (not preferences.requiresDj or venue.hasDj is True)
        and (not preferences.requiresEvents or venue.hostsEvents is True)
        and (not preferences.requiresDogFriendly or venue.dogFriendly is True)
        and (not preferences.requiresWheelchairAccess or venue.wheelchairAccessible is True)
        and (not preferences.requiresReservations or venue.acceptsReservations is True)
        and (not preferences.suitableForGroups or venue.suitableForGroups is True)
        and (not preferences.requiresHappyHour or venue.hasHappyHour is True)
        and (not preferences.requiresSundayRoast or venue.servesSundayRoast is True)
        and (not preferences.requiresVeganOptions or venue.hasVeganOptions is True)
        and (not preferences.requiresQuizNight or venue.hostsQuiz is True)
        and (
            not preferences.requiresAccessibleToilet or venue.hasAccessibleToilet is True
        )
        # An exclusion is only satisfied by a verified negative value. Unknown is not safe.
        and all(featureValue(venue, feature) is False for feature in preferences.excludedFeatures)
    ]
