"""Weighted ranking and explanation tests."""

from app.models.venue import Venue
from app.ranking.venue_ranker import rankVenues
from app.schemas.recommendation import ParsedPreferences


def makeVenue(venueId: str, **overrides: object) -> Venue:
    values: dict[str, object] = {
        "venueId": venueId, "name": venueId, "latitude": 51.5033,
        "longitude": -0.1147, "area": "Waterloo", "priceLevel": "moderate",
        "servesFood": True, "hasOutdoorSeating": True, "showsSports": False,
        "suitableForGroups": True, "noiseLevel": "quiet", "rating": 4.0,
        "popularityScore": 0.7, "tags": [],
    }
    values.update(overrides)
    return Venue.model_validate(values)


def testRankingRewardsPreferenceMatches() -> None:
    matchingVenue = makeVenue("match", priceLevel="cheap", rating=4.5)
    otherVenue = makeVenue("other", priceLevel="expensive", rating=4.5)
    ranked = rankVenues([otherVenue, matchingVenue], ParsedPreferences(priceLevel="cheap"), 2)
    assert [item.venueId for item in ranked] == ["match", "other"]


def testScoresStayNormalised() -> None:
    venues = [makeVenue("high", rating=5.0, popularityScore=1.0),
              makeVenue("low", rating=0.0, popularityScore=0.0)]
    ranked = rankVenues(venues, ParsedPreferences(location="Waterloo"), 2)
    assert all(0.0 <= item.score <= 1.0 for item in ranked)


def testExplanationsAreSupportedByVenueData() -> None:
    venue = makeVenue(
        "explained",
        priceLevel="cheap",
        showsSports=True,
        hasLiveMusic=True,
        hasDj=True,
        hostsEvents=True,
        dogFriendly=True,
        wheelchairAccessible=True,
        acceptsReservations=True,
    )
    preferences = ParsedPreferences(
        location="Waterloo", priceLevel="cheap", requiresFood=True,
        requiresOutdoorSeating=True, showsSports=True, suitableForGroups=True,
        requiresLiveMusic=True, requiresDj=True, requiresEvents=True, requiresDogFriendly=True,
        requiresWheelchairAccess=True, requiresReservations=True, groupSize=5,
        noiseLevel="quiet",
    )
    recommendation = rankVenues([venue], preferences, 1)[0]
    assert "Located in Waterloo" in recommendation.reasons
    assert "Matches the requested price range" in recommendation.reasons
    assert "Serves food" in recommendation.reasons
    assert "Has outdoor seating" in recommendation.reasons
    assert "Shows sports" in recommendation.reasons
    assert "Has live music" in recommendation.reasons
    assert "Has DJ events" in recommendation.reasons
    assert "Hosts events" in recommendation.reasons
    assert "Dog friendly" in recommendation.reasons
    assert "Has verified wheelchair access" in recommendation.reasons
    assert "Accepts table bookings" in recommendation.reasons
    assert "Supports group bookings or private hire" in recommendation.reasons
    assert "Has a quiet atmosphere" in recommendation.reasons
    assert "Hosts events" in recommendation.verifiedFeatures


def testPreferredFeaturesInfluenceRankingWithoutFiltering() -> None:
    preferred = makeVenue("preferred", hasLiveMusic=True)
    other = makeVenue("other", hasLiveMusic=False)

    ranked = rankVenues(
        [other, preferred], ParsedPreferences(preferredFeatures=["liveMusic"]), 2
    )

    assert [item.venueId for item in ranked] == ["preferred", "other"]
    assert "preferredFeatures" in ranked[0].scoreBreakdown
