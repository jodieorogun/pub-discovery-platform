"""Semantic intent parsing tests using small deterministic vectors."""

from app.parsing.query_parser import parseQuery
from app.parsing.semantic_intent_parser import FEATURE_INTENTS, SemanticIntentParser


class KeywordEmbeddingProvider:
    """Map feature concepts to stable one-hot vectors without loading a model."""

    modelName = "keyword-test"
    keywords = {
        "sports": ("sport", "match", "football", "rugby"),
        "food": ("food", "meal", "dinner", "kitchen"),
        "outdoorSeating": ("outdoor", "courtyard", "garden", "alfresco"),
        "liveMusic": ("live music", "band", "musician", "gig"),
        "dj": ("dj", "disc jockey", "decks"),
        "dogFriendly": ("dog", "hound", "pet", "spaniel"),
        "wheelchairAccess": (
            "wheelchair",
            "step-free",
            "steps",
            "threshold",
            "access route",
            "level entry",
        ),
        "reservations": ("reservation", "booking", "reserve", "hold a table"),
        "groups": ("group", "team", "private hire", "function room"),
        "happyHour": ("happy hour", "discounted drinks", "two-for-one"),
        "sundayRoast": ("sunday roast", "roast dinner", "roast lunch"),
        "veganOptions": ("vegan", "vegetarian", "plant-based"),
        "quizNight": ("quiz night", "pub quiz", "trivia"),
        "accessibleToilet": ("accessible toilet", "disabled toilet", "washroom"),
    }

    def _embed(self, text: str) -> list[float]:
        lowered = text.casefold()
        return [
            float(any(keyword in lowered for keyword in self.keywords[feature]))
            for feature in FEATURE_INTENTS
        ]

    def embedPassages(self, passages: list[str]) -> list[list[float]]:
        return [self._embed(passage) for passage in passages]

    def embedQuery(self, query: str) -> list[float]:
        return self._embed(query)


def parser() -> SemanticIntentParser:
    return SemanticIntentParser(KeywordEmbeddingProvider())


def testInfersMultipleUnseenPositiveIntents() -> None:
    query = "courtyard drinks where my hound can join us"

    preferences = parser().enhance(query, parseQuery(query))

    assert preferences.requiresOutdoorSeating is True
    assert preferences.requiresDogFriendly is True


def testUsesBroadNegationScopeForSemanticIntent() -> None:
    query = "pub where dogs are not allowed"

    preferences = parser().enhance(query, parseQuery(query))

    assert preferences.requiresDogFriendly is False
    assert preferences.excludedFeatures == ["dogFriendly"]


def testPropagatesLeadingNegativeInstructionAcrossClauses() -> None:
    query = "avoid venues with a disc jockey"

    preferences = parser().enhance(query, parseQuery(query))

    assert preferences.requiresDj is False
    assert preferences.excludedFeatures == ["dj"]


def testTreatsNoStepsWordingAsPositiveAccess() -> None:
    query = "local reachable without using steps"

    preferences = parser().enhance(query, parseQuery(query))

    assert preferences.requiresWheelchairAccess is True
    assert "wheelchairAccess" not in preferences.excludedFeatures


def testTreatsNoRaisedThresholdAsPositiveAccess() -> None:
    query = "no raised threshold at the door"

    preferences = parser().enhance(query, parseQuery(query))

    assert preferences.requiresWheelchairAccess is True
    assert "wheelchairAccess" not in preferences.excludedFeatures


def testDetectsSemanticContradictionAcrossClauses() -> None:
    query = "dog-friendly but dogs are not allowed"

    preferences = parser().enhance(query, parseQuery(query))

    assert preferences.requiresDogFriendly is False
    assert preferences.contradictions == ["dogFriendly"]


def testDetectsContradictionSeparatedByEvenThough() -> None:
    query = "allows dogs even though pets are refused"

    preferences = parser().enhance(query, parseQuery(query))

    assert preferences.requiresDogFriendly is False
    assert preferences.contradictions == ["dogFriendly"]


def testKeepsSemanticSoftPreferenceOutOfHardFilters() -> None:
    query = "ideally a courtyard for drinks"

    preferences = parser().enhance(query, parseQuery(query))

    assert preferences.requiresOutdoorSeating is False
    assert preferences.preferredFeatures == ["outdoorSeating"]


def testDoesNotTurnSubjectiveMoodIntoAnAmenity() -> None:
    query = "warm traditional boozer full of character"

    preferences = parser().enhance(query, parseQuery(query))

    assert not any(
        getattr(preferences, intent.preferenceField) for intent in FEATURE_INTENTS.values()
    )


def testEnhancesAdditionalFeatureSynonymsIndependently() -> None:
    preferences = parser().enhance(
        "discounted drinks and plant-based dishes with trivia and a disabled toilet",
        parseQuery("discounted drinks and plant-based dishes with trivia and a disabled toilet"),
    )

    assert preferences.requiresHappyHour is True
    assert preferences.requiresVeganOptions is True
    assert preferences.requiresQuizNight is True
    assert preferences.requiresAccessibleToilet is True
    assert preferences.requiresWheelchairAccess is False


def testSundayRoastDoesNotAddASeparateGenericFoodRequirement() -> None:
    preferences = parser().enhance("pub with a Sunday roast", parseQuery("pub with a Sunday roast"))

    assert preferences.requiresSundayRoast is True
    assert preferences.requiresFood is False
