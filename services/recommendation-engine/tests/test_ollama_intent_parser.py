"""Tests for free local structured intent parsing and its safety fallback."""

import json

import httpx

from app.parsing.ollama_intent_parser import OllamaIntentParser
from app.parsing.query_parser import parseQuery
from app.schemas.recommendation import ParsedPreferences


class RecordingFallback:
    """Return a visible preference so tests can prove fallback occurred."""

    version = "test-fallback"

    def enhance(self, query: str, parsed: ParsedPreferences) -> ParsedPreferences:
        return parsed


def _parser(payload: dict[str, list[str]]) -> OllamaIntentParser:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url == "http://127.0.0.1:11434/api/chat"
        assert body["stream"] is False
        assert body["options"]["temperature"] == 0
        assert body["format"]["additionalProperties"] is False
        return httpx.Response(200, json={"message": {"content": json.dumps(payload)}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaIntentParser("test-model", RecordingFallback(), httpClient=client)


def testUsesStructuredLocalModelForUnseenParaphrase() -> None:
    parser = _parser(
        {
            "requiredFeatures": ["wheelchairAccess"],
            "preferredFeatures": [],
            "excludedFeatures": [],
            "contradictions": [],
        }
    )

    preferences = parser.enhance("an entrance my mobility chair can cross", parseQuery("pub"))

    assert preferences.requiresWheelchairAccess is True
    assert preferences.requiresFood is False


def testKeepsNegatedStepFreeSeparateFromPositiveStepFree() -> None:
    parser = _parser(
        {
            "requiredFeatures": [],
            "preferredFeatures": [],
            "excludedFeatures": ["wheelchairAccess"],
            "contradictions": [],
        }
    )

    preferences = parser.enhance("do not include step-free pubs", parseQuery("pub"))

    assert preferences.requiresWheelchairAccess is False
    assert preferences.excludedFeatures == ["wheelchairAccess"]


def testOrdinaryDesiredFeatureCannotBeWeakenedToPreference() -> None:
    parser = _parser(
        {
            "requiredFeatures": [],
            "preferredFeatures": ["dogFriendly"],
            "excludedFeatures": [],
            "contradictions": [],
        }
    )

    preferences = parser.enhance("pub welcoming my puppy", parseQuery("pub"))

    assert preferences.requiresDogFriendly is True
    assert preferences.preferredFeatures == []


def testInventedExclusionWithoutNegativeLanguageIsIgnored() -> None:
    parser = _parser(
        {
            "requiredFeatures": ["outdoorSeating"],
            "preferredFeatures": [],
            "excludedFeatures": ["dogFriendly"],
            "contradictions": [],
        }
    )

    preferences = parser.enhance("an exterior patio", parseQuery("pub"))

    assert preferences.requiresOutdoorSeating is True
    assert preferences.excludedFeatures == []


def testContradictionWinsOverARequiredFeature() -> None:
    parser = _parser(
        {
            "requiredFeatures": ["outdoorSeating"],
            "preferredFeatures": [],
            "excludedFeatures": ["outdoorSeating"],
            "contradictions": ["outdoorSeating"],
        }
    )

    preferences = parser.enhance("outside tables but no outside tables", parseQuery("pub"))

    assert preferences.requiresOutdoorSeating is False
    assert preferences.excludesOutdoorSeating is True
    assert preferences.contradictions == ["outdoorSeating"]


def testMalformedResponseUsesExistingParser() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"message": {"content": "not json"}})
        )
    )
    parser = OllamaIntentParser("test-model", RecordingFallback(), httpClient=client)

    preferences = parser.enhance("anything", parseQuery("pub"))

    assert preferences == parseQuery("pub")


def testRejectsNonLocalOllamaEndpoint() -> None:
    try:
        OllamaIntentParser(
            "test-model",
            RecordingFallback(),
            baseUrl="https://paid.example.com",
        )
    except ValueError as error:
        assert "loopback" in str(error)
    else:
        raise AssertionError("Non-local endpoint was accepted")


def testModelCannotInventAnUnmentionedFeature() -> None:
    parser = _parser(
        {
            "requiredFeatures": ["groups"],
            "preferredFeatures": [],
            "excludedFeatures": [],
            "contradictions": [],
        }
    )

    preferences = parser.enhance("an exterior patio", parseQuery("pub"))

    assert preferences.suitableForGroups is False


def testWithoutStairsDoesNotNegateOtherAnchoredFeatures() -> None:
    parser = _parser(
        {
            "requiredFeatures": ["wheelchairAccess", "reservations"],
            "preferredFeatures": [],
            "excludedFeatures": ["wheelchairAccess", "reservations"],
            "contradictions": [],
        }
    )

    query = "reserve a table reached without stairs"
    preferences = parser.enhance(query, parseQuery(query))

    assert preferences.requiresWheelchairAccess is True
    assert preferences.requiresReservations is True
    assert preferences.excludedFeatures == []


def testLeadingExclusionNegatesTheAnchoredFeature() -> None:
    parser = _parser(
        {
            "requiredFeatures": ["dogFriendly"],
            "preferredFeatures": [],
            "excludedFeatures": [],
            "contradictions": [],
        }
    )

    query = "exclude pubs that permit puppies"
    preferences = parser.enhance(query, parseQuery(query))

    assert preferences.requiresDogFriendly is False
    assert preferences.excludedFeatures == ["dogFriendly"]
