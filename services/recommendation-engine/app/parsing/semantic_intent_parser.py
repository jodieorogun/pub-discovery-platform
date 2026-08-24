"""Use local embeddings to recognise supported feature intents in new wording."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.parsing.query_parser import parseQuery
from app.rag.venue_index import EmbeddingProvider
from app.schemas.recommendation import ParsedPreferences

SEMANTIC_PARSER_VERSION = "semantic-intents-v4-five-features"
TRAINING_EXAMPLES_PATH = Path(__file__).with_name("intent_training_examples_v2.json")


@dataclass(frozen=True)
class FeatureIntent:
    """Configuration for one grounded venue feature."""

    preferenceField: str
    minimumScore: float
    prototypes: tuple[str, ...]


# Prototypes describe concepts rather than enumerating benchmark queries. The
# semantic parser is only enabled when the optional local RAG model is active.
FEATURE_INTENTS: dict[str, FeatureIntent] = {
    "sports": FeatureIntent(
        "showsSports",
        0.64,
        (
            "A pub showing televised live sport, matches and games including football, rugby, "
            "cricket, boxing, tennis and motor racing.",
            "Watch a sporting fixture, tournament or cup final on big television screens while "
            "having a pint.",
            "A pub broadcasting a sports match on television.",
            "Catch football, rugby, cricket or another game on pub screens.",
        ),
    ),
    "food": FeatureIntent(
        "requiresFood",
        0.60,
        (
            "A pub serving food, meals, lunch, dinner, supper, Sunday roast, burgers, snacks and "
            "hot dishes from its kitchen.",
            "Somewhere to eat, dine, grab a bite or order pub grub with drinks.",
            "A pub where cooked food and hot dishes are available.",
            "Order something edible from the kitchen with a pint.",
        ),
    ),
    "outdoorSeating": FeatureIntent(
        "requiresOutdoorSeating",
        0.55,
        (
            "A pub with outdoor seating such as a beer garden, courtyard, terrace, patio, "
            "pavement tables or open-air drinking area.",
            "Sit outside or alfresco in the sunshine and have drinks under the sky.",
            "Customer tables in a pub yard, back garden or exterior space.",
            "Have a pint outdoors in a courtyard or on a rooftop.",
        ),
    ),
    "liveMusic": FeatureIntent(
        "requiresLiveMusic",
        0.67,
        (
            "A pub hosting live music, bands, musicians, singers, acoustic acts, jazz, folk, "
            "gigs, concerts and performances on stage.",
            "Hear performers play instruments or sing live while having drinks.",
            "A band, singer or instrumentalist performing in a pub.",
            "An acoustic, jazz or rock gig staged live at a bar.",
        ),
    ),
    "dj": FeatureIntent(
        "requiresDj",
        0.50,
        (
            "A pub with a DJ, disc jockey, record selector, decks, turntables, dance music and "
            "a dance floor.",
            "Someone mixes and spins records live at a late-night party or club night.",
            "Electronic dance music mixed by a resident selector.",
            "A person spinning vinyl or using decks and turntables.",
        ),
    ),
    "dogFriendly": FeatureIntent(
        "requiresDogFriendly",
        0.63,
        (
            "A dog-friendly pub that welcomes dogs, pets, puppies, pooches, hounds and canine "
            "companions inside.",
            "Bring a spaniel, labrador or terrier for a pint; four-legged animals are allowed.",
            "A pub that permits pets and companion animals inside.",
            "Take a beagle, hound or pooch into the local.",
        ),
    ),
    "wheelchairAccess": FeatureIntent(
        "requiresWheelchairAccess",
        0.52,
        (
            "A wheelchair-accessible pub with step-free, stair-free, flat or level entry.",
            "A disabled guest, wheelchair user or mobility scooter can enter using an accessible "
            "route.",
            "A flat, level doorway with no stairs for a mobility chair user.",
            "An adapted entrance and step-free route for disabled guests.",
        ),
    ),
    "reservations": FeatureIntent(
        "requiresReservations",
        0.69,
        (
            "A pub accepting reservations, advance bookings and prebooked tables or seats.",
            "Book ahead, reserve, arrange, schedule, secure or hold a table before arriving.",
            "The venue saves seats or keeps a table for an advance visit.",
            "Confirm seating beforehand instead of relying on a walk-in table.",
        ),
    ),
    "groups": FeatureIntent(
        "suitableForGroups",
        0.56,
        (
            "A pub suitable for large groups, crowds, parties, teams, companies, departments and "
            "many guests.",
            "A venue with private hire, a function room or event space for birthdays, reunions, "
            "celebrations and work socials.",
            "A large party, office team or crowd can fit together at the venue.",
            "Event or function space for many people and collective gatherings.",
        ),
    ),
    "happyHour": FeatureIntent(
        "requiresHappyHour",
        0.62,
        (
            "A pub offering a happy hour with discounted drinks or two-for-one cocktails.",
            "Reduced-price drinks during a scheduled promotional hour.",
        ),
    ),
    "sundayRoast": FeatureIntent(
        "requiresSundayRoast",
        0.62,
        (
            "A pub serving Sunday roast dinners with roast meat, vegetables and gravy.",
            "Traditional roast lunch served at the pub on Sunday.",
        ),
    ),
    "veganOptions": FeatureIntent(
        "requiresVeganOptions",
        0.62,
        (
            "A pub menu with vegan, vegetarian or plant-based food options.",
            "Plant-based dishes suitable for vegan diners.",
        ),
    ),
    "quizNight": FeatureIntent(
        "requiresQuizNight",
        0.62,
        (
            "A pub hosting a regular quiz night, pub quiz or trivia evening.",
            "Teams answer general knowledge questions at a weekly pub quiz.",
        ),
    ),
    "accessibleToilet": FeatureIntent(
        "requiresAccessibleToilet",
        0.62,
        (
            "A pub with an accessible toilet or disabled toilet for guests.",
            "An adapted accessible washroom or disabled toilet at the venue.",
        ),
    ),
}

NONE_INTENT_PROTOTYPES = (
    "A generic request for a pub, bar, venue, drinks, ale or pints with no specific amenity.",
    "A subjective cosy, historic, traditional or characterful atmosphere.",
    "A lively, relaxed, quiet, intimate, buzzing or festive mood for conversation and drinks.",
    "A charming venue with personality, heritage, stories or an old-fashioned identity.",
    "A location, view, neighbourhood or landmark preference rather than a venue facility.",
)

# Anchors stop a broadly similar mood query from becoming a hard amenity filter.
# Embeddings then validate the meaning around the anchor and handle paraphrases.
FEATURE_ANCHORS: dict[str, re.Pattern[str]] = {
    "sports": re.compile(
        r"\b(?:sport|match|game|contest|footie|football|soccer|rugby|cricket|boxing|tennis|league|"
        r"tournament|fixture|formula|six nations|cup final|broadcast|televis|telly|screen)\w*\b",
        re.IGNORECASE,
    ),
    "food": re.compile(
        r"\b(?:food|eat|feed|meal|lunch|dinner|supper|roast|bite|kitchen|menu|burger|"
        r"snack|dish|chef|grub|dining|edible|cook|plate|fare|cuisine)\w*\b",
        re.IGNORECASE,
    ),
    "outdoorSeating": re.compile(
        r"\b(?:outdoor|outside|exterior|external|garden|courtyard|terrace|patio|yard|rooftop|"
        r"alfresco|open[- ]air|sunshine|fresh air|under the sky|interior|indoors?|indoor-only)\b",
        re.IGNORECASE,
    ),
    "liveMusic": re.compile(
        r"\b(?:music|band|gig|acoustic|jazz|folk|musician|instrumental|act|singer|concert|perform|stage|"
        r"open mic)\w*\b",
        re.IGNORECASE,
    ),
    "dj": re.compile(
        r"\b(?:dj|disc jockey|selector|record selector|guest selector|record mixer|vinyl spinner|"
        r"decks|turntables?|mixing records|spinning records|dj booth)\b|d\.j\.",
        re.IGNORECASE,
    ),
    "dogFriendly": re.compile(
        r"\b(?:dog|pet|pupp(?:y|ies)|pooch|hound|canine|spaniel|labrador|terrier|four[- ]legged|"
        r"companion animal|whippet|dachshund)\w*\b",
        re.IGNORECASE,
    ),
    "wheelchairAccess": re.compile(
        r"\b(?:wheelchair|access route|disabled access|step|stair|level entry|"
        r"level-access|flat[- ]entry|mobility|adapted entrance|flat route|flat path|"
        r"barrier|threshold|chair user)\w*\b",
        re.IGNORECASE,
    ),
    "reservations": re.compile(
        r"\b(?:reservation|booking|prebook|book ahead|book a table|reserve|hold a table|"
        r"table held|arrange a table|schedule a table|secure seats|guarantee.+table|"
        r"pre-arranged table|save.+seats|advance seat|table.+waiting|keeps? tables?|"
        r"planned seating|places? kept|kept ahead|allocat.+tables?|walk-ins?)\w*\b",
        re.IGNORECASE,
    ),
    "groups": re.compile(
        r"\b(?:groups?|crowds?|large crowds?|big crowds?|teams?|corporate|company|department|"
        r"function room|"
        r"private hire|private party|large part(?:y|ies)|hen party|twenty|many guests|reunion|"
        r"work social|room hire|function[- ]room|hire.+room|family gathering|work leaving do|"
        r"sizeable party|large birthday party|accommodat.+party|collective|attendees?|"
        r"colleagues?|coworkers?|organisation)\b",
        re.IGNORECASE,
    ),
    "happyHour": re.compile(
        r"\b(?:happy[- ]hour|discounted drinks?|two[- ]for[- ]one|2[- ]for[- ]1)\b",
        re.IGNORECASE,
    ),
    "sundayRoast": re.compile(
        r"\b(?:sunday roasts?|roast dinners?|roast lunch)\b",
        re.IGNORECASE,
    ),
    "veganOptions": re.compile(
        r"\b(?:vegan|vegetarian|plant[- ]based)\b",
        re.IGNORECASE,
    ),
    "quizNight": re.compile(
        r"\b(?:quiz nights?|pub quiz|weekly quiz|trivia nights?)\b",
        re.IGNORECASE,
    ),
    "accessibleToilet": re.compile(
        r"\b(?:accessible|disabled|wheelchair[- ]accessible) (?:toilets?|loos?|washrooms?)\b",
        re.IGNORECASE,
    ),
}

CLAUSE_SPLITTER = re.compile(
    r"\s*(?:,|;|\b(?:and|but|although|even though|though|despite|plus|with|where|while|"
    r"alongside|before|after|"
    r"for|to|at|on|from)\b)\s*",
    re.IGNORECASE,
)
NEGATIVE_PATTERN = re.compile(
    r"\b(?:no|not|never|without|avoid|avoids|avoiding|omit\w*|exclude|excluding|"
    r"doesn't|doesnt|don't|dont|do not|cannot|can't|cant|unavailable|refused|refusing|away from|"
    r"free of|nothing|zero|leave out|walk-ins?(?:[- ]only)?)\b|"
    r"\b(?:canine|pet|dog)[- ]free\b|"
    r"\bstairs?\s+(?:(?:are|being)\s+)?(?:required|mandatory)\b|"
    r"\brequir\w*\s+stairs?\b|\bmust\s+be\s+inside\b",
    re.IGNORECASE,
)
POSITIVE_ACCESS_PATTERN = re.compile(
    r"\b(?:no|zero)[- ]+(?:stairs?|steps?)\b|"
    r"\bwithout\s+(?:using\s+)?(?:entrance\s+)?(?:steps?|stairs?)\b|"
    r"\b(?:step|stair|barrier)[- ](?:free|less)\b|"
    r"\bno\s+(?:change\s+of\s+level|raised\s+(?:step|threshold))\b",
    re.IGNORECASE,
)
SOFT_PATTERN = re.compile(
    r"\b(?:prefer|preferably|ideally|optional|nice to have|would like)\b",
    re.IGNORECASE,
)
GLOBAL_NEGATIVE_PATTERN = re.compile(
    r"^\s*(?:no|avoid|omit|exclude|leave out|do not|don't|dont|keep us (?:away from|inside))\b|"
    r"\b(?:nothing|away from)\b",
    re.IGNORECASE,
)
CONTRAST_PATTERN = re.compile(
    r"\b(?:but|although|even though|though|yet|despite)\b", re.IGNORECASE
)
INDOOR_PATTERN = re.compile(r"\b(?:indoors?|indoor-only)\b", re.IGNORECASE)
POSITIVE_OUTDOOR_PATTERN = re.compile(
    r"\b(?:beyond|past)\s+the\s+interior\b",
    re.IGNORECASE,
)


def _cosine(left: list[float], right: list[float]) -> float:
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def _clauses(query: str) -> list[str]:
    """Return clauses so separate requirements can be classified independently."""
    splitClauses = [part.strip() for part in CLAUSE_SPLITTER.split(query) if part.strip()]
    return list(dict.fromkeys(value for value in splitClauses if len(value) >= 3))


def _isNegative(clause: str) -> bool:
    """Recognise negative scope without misreading positive step-free wording."""
    if POSITIVE_ACCESS_PATTERN.search(clause) and not re.search(
        r"\b(?:no|not|without)\s+(?:a\s+)?(?:step|stair|barrier)[- ]free\b",
        clause,
        re.IGNORECASE,
    ):
        return False
    return bool(NEGATIVE_PATTERN.search(clause))


class SemanticIntentParser:
    """Enhance deterministic preferences with high-confidence semantic intents."""

    version = SEMANTIC_PARSER_VERSION

    def __init__(self, provider: EmbeddingProvider) -> None:
        self.provider = provider
        prototypeTexts = [
            prototype for intent in FEATURE_INTENTS.values() for prototype in intent.prototypes
        ]
        prototypeTexts.extend(NONE_INTENT_PROTOTYPES)
        prototypeEmbeddings = provider.embedPassages(prototypeTexts)
        if len(prototypeEmbeddings) != len(prototypeTexts):
            raise ValueError("Embedding provider returned the wrong number of intent vectors")

        self._embeddings: dict[str, list[list[float]]] = {}
        offset = 0
        for feature, intent in FEATURE_INTENTS.items():
            end = offset + len(intent.prototypes)
            self._embeddings[feature] = prototypeEmbeddings[offset:end]
            offset = end
        self._noneEmbeddings = prototypeEmbeddings[offset:]

        # Development examples provide semantic variety beyond the short
        # prototypes. They contain no venue labels or holdout-v4 queries.
        trainingPayload: dict[str, Any] = json.loads(
            TRAINING_EXAMPLES_PATH.read_text(encoding="utf-8")
        )
        examples = trainingPayload.get("examples")
        if not isinstance(examples, list):
            raise ValueError("Intent training examples must be a list")
        exampleTexts = [str(example["text"]) for example in examples]
        exampleEmbeddings = provider.embedPassages(exampleTexts)
        if len(exampleEmbeddings) != len(examples):
            raise ValueError("Embedding provider returned the wrong number of example vectors")
        for example, embedding in zip(examples, exampleEmbeddings, strict=True):
            feature = str(example["feature"])
            if feature == "none":
                self._noneEmbeddings.append(embedding)
            elif feature in self._embeddings:
                self._embeddings[feature].append(embedding)
            else:
                raise ValueError(f"Unsupported intent training feature: {feature}")

    def enhance(self, query: str, parsed: ParsedPreferences) -> ParsedPreferences:
        """Add only confident intents, exclusions, and contradictions."""
        required = {
            feature
            for feature, intent in FEATURE_INTENTS.items()
            if getattr(parsed, intent.preferenceField)
        }
        excluded = set(parsed.excludedFeatures)
        preferred = set(parsed.preferredFeatures)
        explicitPositive: set[str] = set()
        negativeOverrides: set[str] = set()
        globalNegative = (
            bool(GLOBAL_NEGATIVE_PATTERN.search(query))
            and not bool(CONTRAST_PATTERN.search(query))
            and not bool(POSITIVE_ACCESS_PATTERN.search(query))
        )

        for clause in _clauses(query):
            clauseParsed = parseQuery(clause)
            clauseRequired = {
                feature
                for feature, intent in FEATURE_INTENTS.items()
                if getattr(clauseParsed, intent.preferenceField)
            }
            negative = globalNegative or _isNegative(clause)
            if negative:
                # Wider negative scope fixes wording such as "does not show football".
                negativeOverrides.update(clauseRequired)
                excluded.update(clauseRequired)
            else:
                explicitPositive.update(clauseRequired)

            queryEmbedding = self.provider.embedQuery(clause)
            scores = {
                feature: max(
                    _cosine(queryEmbedding, prototype) for prototype in self._embeddings[feature]
                )
                for feature in FEATURE_INTENTS
            }
            anchored = {
                feature for feature, pattern in FEATURE_ANCHORS.items() if pattern.search(clause)
            }
            candidates = set(anchored)

            # Fall back to the closest semantic intent only when it beats the
            # generic/subjective prototypes. This covers new vocabulary without
            # turning atmosphere requests into hard amenity filters.
            topFeature = max(scores, key=lambda feature: scores[feature])
            rankedScores = sorted(scores.values(), reverse=True)
            noneScore = max(
                _cosine(queryEmbedding, prototype) for prototype in self._noneEmbeddings
            )
            if (
                not globalNegative
                and scores[topFeature] >= FEATURE_INTENTS[topFeature].minimumScore
                and scores[topFeature] >= noneScore + 0.04
                and scores[topFeature] >= rankedScores[1] + 0.04
            ):
                candidates.add(topFeature)

            # Toilet accessibility and entrance accessibility are separate
            # claims. A toilet-only phrase must not acquire the broader
            # wheelchairAccess intent from semantic similarity.
            if "accessibleToilet" in anchored and "wheelchairAccess" not in anchored:
                candidates.discard("wheelchairAccess")
            if "sundayRoast" in anchored and "food" not in clauseRequired:
                candidates.discard("food")

            # Negated and soft clauses should affect only the explicit anchor,
            # or one clear semantic fallback when no anchor exists.
            if negative or SOFT_PATTERN.search(clause):
                candidates = set(anchored) or ({topFeature} if topFeature in candidates else set())

            # A single clause normally means either a DJ or live performers,
            # not both. Separate clauses can still request both explicitly.
            if {"dj", "liveMusic"} <= candidates:
                protectedEvents = {"dj", "liveMusic"} & clauseRequired
                lowerEventFeature = (
                    ({"dj", "liveMusic"} - protectedEvents).pop()
                    if len(protectedEvents) == 1
                    else min(("dj", "liveMusic"), key=lambda feature: scores[feature])
                )
                candidates.remove(lowerEventFeature)

            for feature in candidates:
                score = scores[feature]
                if score < FEATURE_INTENTS[feature].minimumScore and not (
                    negative and feature in anchored
                ):
                    continue

                if SOFT_PATTERN.search(clause):
                    preferred.add(feature)
                elif negative or (
                    feature == "outdoorSeating"
                    and INDOOR_PATTERN.search(clause)
                    and not POSITIVE_OUTDOOR_PATTERN.search(clause)
                ):
                    negativeOverrides.add(feature)
                    excluded.add(feature)
                else:
                    explicitPositive.add(feature)
                    required.add(feature)

        # Use the complete positive query as a capped two-label classifier.
        # This restores context lost by clause splitting without allowing a
        # single example to activate many unrelated hard filters.
        if not (
            globalNegative
            or CONTRAST_PATTERN.search(query)
            or NEGATIVE_PATTERN.search(query)
            or INDOOR_PATTERN.search(query)
            or SOFT_PATTERN.search(query)
            or any(pattern.search(query) for pattern in FEATURE_ANCHORS.values())
        ):
            queryEmbedding = self.provider.embedQuery(query)
            fullScores = {
                feature: max(
                    _cosine(queryEmbedding, prototype) for prototype in self._embeddings[feature]
                )
                for feature in FEATURE_INTENTS
            }
            noneScore = max(
                _cosine(queryEmbedding, prototype) for prototype in self._noneEmbeddings
            )
            rankedFeatures = sorted(
                fullScores,
                key=lambda feature: fullScores[feature],
                reverse=True,
            )
            bestScore = fullScores[rankedFeatures[0]]
            for feature in rankedFeatures[:2]:
                score = fullScores[feature]
                if (
                    score >= FEATURE_INTENTS[feature].minimumScore
                    and score >= noneScore + 0.04
                    and score >= bestScore - 0.02
                ):
                    required.add(feature)
                    explicitPositive.add(feature)

        required -= negativeOverrides - explicitPositive
        contradictions = set(parsed.contradictions) | (explicitPositive & excluded)
        required -= contradictions
        updates: dict[str, object] = {
            "excludedFeatures": sorted(excluded),
            "preferredFeatures": sorted(preferred),
            "contradictions": sorted(contradictions),
            "excludesOutdoorSeating": "outdoorSeating" in excluded,
        }
        for feature, intent in FEATURE_INTENTS.items():
            updates[intent.preferenceField] = feature in required
        return parsed.model_copy(update=updates)
