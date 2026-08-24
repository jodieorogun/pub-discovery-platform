"""Candidate hard-filter tests."""

from app.models.venue import Venue
from app.parsing.query_parser import parseQuery
from app.retrieval.candidate_retriever import retrieveCandidates
from app.schemas.recommendation import ParsedPreferences


def makeVenue(**overrides: object) -> Venue:
    values: dict[str, object] = {
        "venueId": "venue-test",
        "name": "Test Pub",
        "latitude": 51.5,
        "longitude": -0.1,
        "area": "Waterloo",
        "priceLevel": "moderate",
        "servesFood": True,
        "hasOutdoorSeating": True,
        "showsSports": False,
        "suitableForGroups": True,
        "noiseLevel": "quiet",
        "rating": 4.0,
        "popularityScore": 0.7,
        "tags": [],
    }
    values.update(overrides)
    return Venue.model_validate(values)


def testAppliesHardFilters() -> None:
    matchingVenue = makeVenue()
    candidates = retrieveCandidates(
        [
            matchingVenue,
            makeVenue(venueId="wrong-area", area="Camden"),
            makeVenue(venueId="no-food", servesFood=False),
        ],
        ParsedPreferences(location="Waterloo", requiresFood=True),
    )
    assert candidates == [matchingVenue]


def testDoesNotHardFilterSoftPreferences() -> None:
    venue = makeVenue(priceLevel="expensive", noiseLevel="lively")
    candidates = retrieveCandidates(
        [venue], ParsedPreferences(priceLevel="cheap", noiseLevel="quiet")
    )
    assert candidates == [venue]


def testFiltersForVerifiedGroupSuitability() -> None:
    matchingVenue = makeVenue(venueId="groups", suitableForGroups=True)

    candidates = retrieveCandidates(
        [
            matchingVenue,
            makeVenue(venueId="not-groups", suitableForGroups=False),
            makeVenue(venueId="unknown-groups", suitableForGroups=None),
        ],
        ParsedPreferences(suitableForGroups=True),
    )

    assert candidates == [matchingVenue]


def testFiltersForExplicitlyExcludedOutdoorSeating() -> None:
    noOutdoorSeating = makeVenue(venueId="no-outdoor", hasOutdoorSeating=False)
    candidates = retrieveCandidates(
        [
            noOutdoorSeating,
            makeVenue(venueId="has-outdoor", hasOutdoorSeating=True),
            makeVenue(venueId="unknown-outdoor", hasOutdoorSeating=None),
        ],
        ParsedPreferences(excludesOutdoorSeating=True),
    )
    assert candidates == [noOutdoorSeating]


def testFiltersNewExplicitFeatureRequirements() -> None:
    matchingVenue = makeVenue(
        hasLiveMusic=True,
        hasDj=True,
        hostsEvents=True,
        dogFriendly=True,
        wheelchairAccessible=True,
        acceptsReservations=True,
    )
    candidates = retrieveCandidates(
        [matchingVenue, makeVenue(venueId="unknown-features")],
        ParsedPreferences(
            requiresLiveMusic=True,
            requiresDj=True,
            requiresEvents=True,
            requiresDogFriendly=True,
            requiresWheelchairAccess=True,
            requiresReservations=True,
        ),
    )
    assert candidates == [matchingVenue]


def testFiltersFiveAdditionalVerifiedFeatures() -> None:
    matchingVenue = makeVenue(
        hasHappyHour=True,
        servesSundayRoast=True,
        hasVeganOptions=True,
        hostsQuiz=True,
        hasAccessibleToilet=True,
    )
    preferences = ParsedPreferences(
        requiresHappyHour=True,
        requiresSundayRoast=True,
        requiresVeganOptions=True,
        requiresQuizNight=True,
        requiresAccessibleToilet=True,
    )

    assert retrieveCandidates(
        [matchingVenue, makeVenue(venueId="unknown-additional-features")], preferences
    ) == [matchingVenue]


def testFiltersGeneralExclusionsAndRejectsUnknownValues() -> None:
    verifiedNonSportsVenue = makeVenue(venueId="no-sports", showsSports=False)

    candidates = retrieveCandidates(
        [
            verifiedNonSportsVenue,
            makeVenue(venueId="sports", showsSports=True),
            makeVenue(venueId="unknown", showsSports=None),
        ],
        ParsedPreferences(excludedFeatures=["sports"]),
    )

    assert candidates == [verifiedNonSportsVenue]


def testFiltersNegativeStepFreeRequestUsingVerifiedValues() -> None:
    verifiedSteps = makeVenue(venueId="has-steps", wheelchairAccessible=False)

    candidates = retrieveCandidates(
        [
            verifiedSteps,
            makeVenue(venueId="step-free", wheelchairAccessible=True),
            makeVenue(venueId="unknown-access", wheelchairAccessible=None),
        ],
        parseQuery("pub with no step-free access"),
    )

    assert candidates == [verifiedSteps]


def testContradictionsReturnNoCandidates() -> None:
    assert retrieveCandidates([makeVenue()], ParsedPreferences(contradictions=["sports"])) == []


def testFiltersLocationAndPriceExclusions() -> None:
    matchingVenue = makeVenue(venueId="match", area="Waterloo", priceLevel="moderate")

    candidates = retrieveCandidates(
        [
            matchingVenue,
            makeVenue(venueId="camden", area="Camden"),
            makeVenue(venueId="expensive", priceLevel="expensive"),
            makeVenue(venueId="unknown-price", priceLevel=None),
        ],
        ParsedPreferences(excludedLocations=["Camden"], excludedPriceLevels=["expensive"]),
    )

    assert candidates == [matchingVenue]
