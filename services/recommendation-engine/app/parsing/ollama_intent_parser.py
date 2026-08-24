"""Use a free local Ollama model to turn queries into structured intents."""

from __future__ import annotations

import logging
import re
from typing import Literal, Protocol
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.parsing.semantic_intent_parser import (
    FEATURE_ANCHORS,
    FEATURE_INTENTS,
    GLOBAL_NEGATIVE_PATTERN,
    POSITIVE_ACCESS_PATTERN,
)
from app.schemas.recommendation import ParsedPreferences

OLLAMA_PARSER_VERSION = "local-ollama-v4-five-features"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

logger = logging.getLogger(__name__)

SupportedFeature = Literal[
    "sports",
    "food",
    "outdoorSeating",
    "liveMusic",
    "dj",
    "dogFriendly",
    "wheelchairAccess",
    "reservations",
    "groups",
    "happyHour",
    "sundayRoast",
    "veganOptions",
    "quizNight",
    "accessibleToilet",
]


class IntentParser(Protocol):
    """Small interface shared by local intent parsers."""

    version: str

    def enhance(self, query: str, parsed: ParsedPreferences) -> ParsedPreferences: ...


class OllamaIntentResult(BaseModel):
    """Strict output accepted from the local model."""

    model_config = ConfigDict(extra="forbid")

    requiredFeatures: list[SupportedFeature] = Field(default_factory=list, max_length=14)
    preferredFeatures: list[SupportedFeature] = Field(default_factory=list, max_length=14)
    excludedFeatures: list[SupportedFeature] = Field(default_factory=list, max_length=14)
    contradictions: list[SupportedFeature] = Field(default_factory=list, max_length=14)


SYSTEM_PROMPT = """You classify a pub recommendation query into supported venue features.
Return only JSON matching the supplied schema.

Supported features:
- sports: televised sport
- food: meals or food service
- outdoorSeating: beer garden, terrace, courtyard, patio, or outside tables
- liveMusic: live performers, bands, singers, or instrumentalists
- dj: a DJ, decks, or a record selector
- dogFriendly: dogs or pets are allowed
- wheelchairAccess: wheelchair accessible, step-free, stair-free, or level access
- reservations: advance table booking
- groups: suitable for a large group, party, function, or private hire
- happyHour: a clearly advertised happy hour or timed drinks promotion
- sundayRoast: Sunday roast meals
- veganOptions: vegan, vegetarian, or plant-based food choices
- quizNight: a pub quiz or trivia night
- accessibleToilet: an accessible or disabled toilet (not merely an accessible entrance)

Rules:
- A normal recommendation request makes every positively mentioned feature required. It does not
  need to contain words such as "must" or "required".
- requiredFeatures contains the desired features unless explicitly softened or negated.
- preferredFeatures contains optional wording such as preferably or ideally.
- excludedFeatures contains features the user rejects or says must be absent.
- contradictions contains a feature explicitly required and rejected in the same query.
- Negation changes the meaning. "step-free" requires wheelchairAccess, while "not step-free"
  excludes it. "no steps" requires wheelchairAccess. "no outdoor tables" excludes
  outdoorSeating.
- Do not infer facilities from mood, location, price, history, or generic pub wording.
- Do not add related features: liveMusic is not a DJ, and groups is not reservations.
- Positive permission words are not negation. "welcomes", "permits", "accepts", "admits",
  "available", "may enter", and "can enter" indicate a required feature.
- "external", "outside", and "open sky" require outdoorSeating. "indoors" alone does not reject
  dogs, food, access, or another unrelated feature.
- Never exclude a feature merely because the request is indirect or uncertain.
- Use empty lists when no supported feature is requested.
"""

FEW_SHOT_MESSAGES = [
    {
        "role": "user",
        "content": "external deck that admits our greyhound",
    },
    {
        "role": "assistant",
        "content": (
            '{"requiredFeatures":["outdoorSeating","dogFriendly"],'
            '"preferredFeatures":[],"excludedFeatures":[],"contradictions":[]}'
        ),
    },
    {
        "role": "user",
        "content": "ideally a live singer but no DJ",
    },
    {
        "role": "assistant",
        "content": (
            '{"requiredFeatures":[],"preferredFeatures":["liveMusic"],'
            '"excludedFeatures":["dj"],"contradictions":[]}'
        ),
    },
    {"role": "user", "content": "entrance with no steps"},
    {
        "role": "assistant",
        "content": (
            '{"requiredFeatures":["wheelchairAccess"],"preferredFeatures":[],'
            '"excludedFeatures":[],"contradictions":[]}'
        ),
    },
    {
        "role": "user",
        "content": "show the match but do not show any sport",
    },
    {
        "role": "assistant",
        "content": (
            '{"requiredFeatures":["sports"],"preferredFeatures":[],'
            '"excludedFeatures":["sports"],"contradictions":["sports"]}'
        ),
    },
]

SOFT_REQUEST_PATTERN = re.compile(
    r"\b(?:prefer|preferably|ideally|optional|nice to have|would like)\b", re.IGNORECASE
)
NEGATIVE_REQUEST_PATTERN = re.compile(
    r"\b(?:no|not|never|without|avoid|omit|exclude|forbid\w*|refus\w*|"
    r"do not|don't|dont|cannot|can't|cant|walk-in-only)\b",
    re.IGNORECASE,
)
CONTRAST_REQUEST_PATTERN = re.compile(
    r"\b(?:but|although|though|while|yet|despite|even though)\b", re.IGNORECASE
)


def _validateLocalUrl(baseUrl: str) -> str:
    """Allow only loopback Ollama endpoints so queries never leave this computer."""
    parsed = urlparse(baseUrl)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("OLLAMA_BASE_URL must be an HTTP loopback address")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("OLLAMA_BASE_URL must not contain credentials, a query, or a fragment")
    return baseUrl.rstrip("/")


class OllamaIntentParser:
    """Prefer structured local-LLM output and safely fall back when unavailable."""

    version = OLLAMA_PARSER_VERSION

    def __init__(
        self,
        model: str,
        fallback: IntentParser,
        baseUrl: str = DEFAULT_OLLAMA_URL,
        timeoutSeconds: float = 8.0,
        httpClient: httpx.Client | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("An Ollama model name is required")
        self.model = model.strip()
        self.fallback = fallback
        self.baseUrl = _validateLocalUrl(baseUrl)
        self._ownsClient = httpClient is None
        self._client = httpClient or httpx.Client(timeout=timeoutSeconds)

    def enhance(self, query: str, parsed: ParsedPreferences) -> ParsedPreferences:
        """Classify one query, returning the existing semantic parser on any local failure."""
        # The proven semantic parser remains the floor. The small LLM can add
        # nuance, but it cannot erase the established local interpretation.
        fallbackParsed = self.fallback.enhance(query, parsed)
        try:
            result = self._classify(query)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            # Ollama is optional. A missing server, model, or malformed response
            # must never make the recommendation endpoint unavailable.
            logger.warning("Local Ollama intent parsing failed; using semantic fallback: %s", error)
            return fallbackParsed
        return _mergeResult(query, fallbackParsed, result)

    def _classify(self, query: str) -> OllamaIntentResult:
        """Request deterministic JSON and validate every returned field."""
        response = self._client.post(
            f"{self.baseUrl}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "format": OllamaIntentResult.model_json_schema(),
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *FEW_SHOT_MESSAGES,
                    {"role": "user", "content": query},
                ],
                "options": {"temperature": 0, "seed": 0},
            },
        )
        response.raise_for_status()
        content = response.json()["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("Ollama message content must be a JSON string")
        return OllamaIntentResult.model_validate_json(content)

    def close(self) -> None:
        """Release the internally created connection pool."""
        if self._ownsClient:
            self._client.close()


def _mergeResult(
    query: str, parsed: ParsedPreferences, result: OllamaIntentResult
) -> ParsedPreferences:
    """Merge validated model output without discarding deterministic rule matches."""
    ruleRequired = {
        feature
        for feature, intent in FEATURE_INTENTS.items()
        if getattr(parsed, intent.preferenceField)
    }
    anchored = {
        feature for feature, pattern in FEATURE_ANCHORS.items() if pattern.search(query)
    }
    modelRequired = set(result.requiredFeatures) & anchored
    modelPreferred = set(result.preferredFeatures) & anchored
    if (
        NEGATIVE_REQUEST_PATTERN.search(query)
        and not CONTRAST_REQUEST_PATTERN.search(query)
        and not POSITIVE_ACCESS_PATTERN.search(query)
    ):
        modelRequired.clear()
        modelPreferred.clear()
    required = ruleRequired | modelRequired
    preferred = set(parsed.preferredFeatures) | modelPreferred

    # Small local models sometimes call an ordinary request a preference. Only
    # explicit soft language is allowed to weaken a venue requirement.
    if not SOFT_REQUEST_PATTERN.search(query):
        required.update(preferred)
        preferred.clear()

    # Likewise, an exclusion is unsafe unless the user actually used negative
    # language. This blocks hallucinations such as treating "pets accepted" as
    # dog-free while still letting the model resolve which feature is negated.
    modelExcluded = (
        set(result.excludedFeatures) & anchored
        if NEGATIVE_REQUEST_PATTERN.search(query)
        else set()
    )
    positiveAccessMatch = POSITIVE_ACCESS_PATTERN.search(query)
    if positiveAccessMatch:
        remainingQuery = (
            query[: positiveAccessMatch.start()] + query[positiveAccessMatch.end() :]
        )
        if not NEGATIVE_REQUEST_PATTERN.search(remainingQuery):
            # "Without stairs" is positive access, not a global request to
            # exclude every other feature mentioned in the sentence.
            modelExcluded.clear()
    excluded = set(parsed.excludedFeatures) | modelExcluded
    if (
        GLOBAL_NEGATIVE_PATTERN.search(query)
        and not CONTRAST_REQUEST_PATTERN.search(query)
        and not positiveAccessMatch
    ):
        # Leading instructions such as "exclude pubs that permit puppies"
        # negate the anchored feature even when the small model misses scope.
        excluded.update(anchored)
        required -= anchored
    contradictions = (
        set(parsed.contradictions)
        | (
            set(result.contradictions) & anchored
            if NEGATIVE_REQUEST_PATTERN.search(query)
            else set()
        )
        | (required & excluded)
    )

    # Contradictory requirements should produce no candidates, not an arbitrary
    # interpretation of which side the user meant.
    required -= contradictions
    preferred -= excluded | required | contradictions

    updates: dict[str, object] = {
        "preferredFeatures": sorted(preferred),
        "excludedFeatures": sorted(excluded),
        "contradictions": sorted(contradictions),
        "excludesOutdoorSeating": "outdoorSeating" in excluded,
    }
    for feature, intent in FEATURE_INTENTS.items():
        updates[intent.preferenceField] = feature in required
    return parsed.model_copy(update=updates)
