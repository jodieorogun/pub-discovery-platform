"""Deterministic query parser tests."""

from app.parsing.query_parser import parseQuery


def testParsesSupportedPreferences() -> None:
    preferences = parseQuery(
        "Moderate group pub near London Bridge with food, football and a beer garden"
    )
    assert preferences.location == "London Bridge"
    assert preferences.priceLevel == "moderate"
    assert preferences.requiresFood is True
    assert preferences.requiresOutdoorSeating is True
    assert preferences.showsSports is True
    assert preferences.suitableForGroups is True
    assert preferences.unparsedTerms == []


def testParsesNoisePreferences() -> None:
    assert parseQuery("a quiet pub in Camden").noiseLevel == "quiet"
    assert parseQuery("a lively Brixton pub").noiseLevel == "lively"
    assert parseQuery("a pub in Westminster").location == "Westminster"


def testParsesExcludedOutdoorSeating() -> None:
    for query in (
        "pubs in Westminster with food and no outdoor seating",
        "pub in Westminster without outdoor seating",
        "pub in Westminster with no beer garden",
    ):
        preferences = parseQuery(query)
        assert preferences.requiresOutdoorSeating is False
        assert preferences.excludesOutdoorSeating is True
        assert preferences.unparsedTerms == []


def testPreservesUnknownSearchTerms() -> None:
    preferences = parseQuery("cheap rooftop pub in Shoreditch with karaoke")
    assert preferences.unparsedTerms == ["rooftop", "karaoke"]


def testParsesPartySizeAndNewVerifiedFeatures() -> None:
    preferences = parseQuery(
        "dog-friendly wheelchair accessible pub for 5 people in Westminster "
        "with live music, a DJ and table bookings"
    )
    assert preferences.location == "Westminster"
    assert preferences.groupSize == 5
    assert preferences.suitableForGroups is True
    assert preferences.requiresLiveMusic is True
    assert preferences.requiresDj is True
    assert preferences.requiresDogFriendly is True
    assert preferences.requiresWheelchairAccess is True
    assert preferences.requiresReservations is True
    assert preferences.unparsedTerms == []


def testParsesNaturalFeatureWordingAsVerifiedRequirements() -> None:
    preferences = parseQuery(
        "step-free pub garden for lunch where we can watch the match with our puppy"
    )

    assert preferences.requiresWheelchairAccess is True
    assert preferences.requiresOutdoorSeating is True
    assert preferences.requiresFood is True
    assert preferences.showsSports is True
    assert preferences.requiresDogFriendly is True


def testParsesEventsWithNegationAndContradictions() -> None:
    assert parseQuery("Camden pub with regular events").requiresEvents is True

    excluded = parseQuery("Camden pub with no events")
    assert excluded.requiresEvents is False
    assert "events" in excluded.excludedFeatures

    contradictory = parseQuery("pub with events but no events")
    assert contradictory.contradictions == ["events"]


def testParsesFiveNewGroundedFeaturesAndNegations() -> None:
    preferences = parseQuery(
        "Camden pub with happy hour, Sunday roast, vegan options, a quiz night "
        "and an accessible toilet"
    )

    assert preferences.requiresHappyHour is True
    assert preferences.requiresSundayRoast is True
    assert preferences.requiresVeganOptions is True
    assert preferences.requiresQuizNight is True
    assert preferences.requiresAccessibleToilet is True
    assert preferences.requiresWheelchairAccess is False

    excluded = parseQuery("pub with no happy hour and without a quiz night")
    assert excluded.requiresHappyHour is False
    assert excluded.requiresQuizNight is False
    assert excluded.excludedFeatures == ["happyHour", "quizNight"]


def testDistinguishesPositiveAndNegativeStepFreeRequests() -> None:
    positive = parseQuery("step-free pub")
    assert positive.requiresWheelchairAccess is True
    assert "wheelchairAccess" not in positive.excludedFeatures

    for query in (
        "pub with no step-free access",
        "pub that is not wheelchair accessible",
        "pub without wheelchair access",
    ):
        negative = parseQuery(query)
        assert negative.requiresWheelchairAccess is False
        assert "wheelchairAccess" in negative.excludedFeatures


def testUnderstandsNoEntranceStepsAsPositiveAccessibility() -> None:
    preferences = parseQuery("pub without entrance steps")

    assert preferences.requiresWheelchairAccess is True
    assert "wheelchairAccess" not in preferences.excludedFeatures


def testReportsContradictoryAccessibilityRequirements() -> None:
    preferences = parseQuery("step-free but not wheelchair accessible")

    assert preferences.requiresWheelchairAccess is False
    assert preferences.contradictions == ["wheelchairAccess"]


def testParsesExplicitGroupOccasionsAsARequirement() -> None:
    assert parseQuery("office party pub venue").suitableForGroups is True
    assert parseQuery("relaxed birthday drinks").suitableForGroups is False


def testParsesGeneralFeatureExclusions() -> None:
    preferences = parseQuery("quiet pub with no sports and without food")

    assert preferences.showsSports is False
    assert preferences.requiresFood is False
    assert preferences.excludedFeatures == ["food", "sports"]
    assert preferences.noiseLevel == "quiet"
    assert preferences.unparsedTerms == []


def testParsesSoftPreferencesWithoutMakingThemHardRequirements() -> None:
    preferences = parseQuery("pub that would prefer live music and ideally a beer garden")

    assert preferences.requiresLiveMusic is False
    assert preferences.requiresOutdoorSeating is False
    assert preferences.preferredFeatures == ["liveMusic", "outdoorSeating"]


def testReportsContradictoryFeatureMentions() -> None:
    preferences = parseQuery("pub with sports but no sports")

    assert preferences.showsSports is False
    assert preferences.contradictions == ["sports"]


def testParsesLocationAndPriceExclusions() -> None:
    preferences = parseQuery("pub not in Camden and not expensive")

    assert preferences.location is None
    assert preferences.excludedLocations == ["Camden"]
    assert preferences.priceLevel is None
    assert preferences.excludedPriceLevels == ["expensive"]
    assert preferences.unparsedTerms == []
