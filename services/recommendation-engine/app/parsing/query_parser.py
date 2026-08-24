"""Deterministic parser for supported venue preferences."""

import re

from app.schemas.recommendation import ParsedPreferences

SUPPORTED_AREAS = (
    "Waterloo",
    "London Bridge",
    "Shoreditch",
    "Brixton",
    "Camden",
    "Westminster",
)
PARSER_VERSION = "rules-v4-five-features"
FILLER_TERMS = {
    "a",
    "an",
    "and",
    "at",
    "bar",
    "but",
    "can",
    "find",
    "for",
    "have",
    "in",
    "like",
    "me",
    "must",
    "near",
    "of",
    "our",
    "please",
    "pub",
    "pubs",
    "somewhere",
    "that",
    "the",
    "to",
    "want",
    "we",
    "where",
    "with",
    "would",
}
GROUP_SIZE_PATTERN = re.compile(
    r"\b(?:(?:for|party of|group of)\s+(\d{1,2})(?:\s+(?:people|persons|friends|guests))?"
    r"|(\d{1,2})\s+(?:people|persons|friends|guests))\b"
)

# Canonical names are shared by exclusions, soft preferences, and API diagnostics.
FEATURE_PHRASES: dict[str, tuple[str, ...]] = {
    # Keep these phrases explicit. A match becomes a verified hard requirement,
    # so broad mood words should remain available to semantic retrieval instead.
    "food": (
        "food",
        "lunch",
        "dinner",
        "menu",
        "meal",
        "meals",
        "gastropub",
        "kitchen",
    ),
    "outdoorSeating": (
        "outdoor seating",
        "outdoor pub seating",
        "beer garden",
        "drinks outside",
        "pub terrace",
        "outside tables",
        "pub garden",
        "pub patio",
        "outdoor area",
        "sit outside",
        "open-air pub tables",
    ),
    "sports": (
        "sports",
        "sport",
        "football",
        "watch the match",
        "rugby",
        "sky sports",
        "tv screens",
        "screening the big game",
        "premier league",
    ),
    "liveMusic": (
        "live music",
        "live band",
        "bands playing",
        "acoustic music",
        "music nights",
        "rock music",
        "hosting musicians",
        "music performance",
        "live performers",
    ),
    "dj": ("dj night", "dj nights", "dj set", "dj sets", "djs", "dj"),
    "events": (
        "events",
        "event nights",
        "what's on",
        "whats on",
        "regular events",
    ),
    "dogFriendly": (
        "dog friendly",
        "dog-friendly",
        "dogs welcome",
        "dog is welcome",
        "bring a dog",
        "pub for dogs",
        "my puppy",
        "our puppy",
        "pet-friendly",
        "dogs come",
        "four-legged friends",
        "dogs are allowed",
    ),
    "wheelchairAccess": (
        "wheelchair accessible",
        "wheelchair access",
        "step free",
        "step-free",
        "accessible pub entrance",
        "suitable for a wheelchair",
        # This is positive wording: no entrance steps means step-free access.
        "without entrance steps",
        "wheelchair-friendly",
        "disabled access",
    ),
    "reservations": (
        "book a table",
        "table booking",
        "table bookings",
        "reservations",
        "reservation",
        "bookable",
        "reserve a table",
        "accepting bookings",
        "table booking",
        "booked table",
        "book a pub space",
        "reserve seats",
    ),
    "groups": (
        "group",
        "groups",
        "large birthday group",
        "private hire",
        "group booking",
        "private party",
        "space for a celebration",
        "large gathering",
        "pub room hire",
        "office party",
        "birthday gathering",
        "private space",
        "accommodates groups",
        "large group",
    ),
    "happyHour": (
        "happy hour",
        "happy-hour",
        "discounted drinks",
        "two for one drinks",
        "2 for 1 cocktails",
    ),
    "sundayRoast": (
        "sunday roast",
        "sunday roasts",
        "roast dinner",
        "roast dinners",
    ),
    "veganOptions": (
        "vegan options",
        "vegan food",
        "vegan menu",
        "vegan dishes",
        "vegetarian options",
        "vegetarian menu",
    ),
    "quizNight": (
        "quiz night",
        "quiz nights",
        "pub quiz",
        "weekly quiz",
        "trivia night",
    ),
    "accessibleToilet": (
        "accessible toilet",
        "accessible toilets",
        "accessible loo",
        "disabled toilet",
        "wheelchair accessible toilet",
    ),
}
NEGATIVE_PREFIXES = ("no", "not", "without", "avoid")
PREFERENCE_PREFIXES = ("prefer", "preferably", "ideally", "nice to have")


def _phraseStrengths(query: str, phrase: str) -> set[str]:
    """Classify all mentions of a feature, including contradictory mentions."""
    escapedPhrase = re.escape(phrase)
    negativeCount = len(
        re.findall(
            rf"\b(?:{'|'.join(NEGATIVE_PREFIXES)})\s+"
            rf"(?:(?:be|have|show|serve|allow|offer|host)\s+)?"
            rf"(?:in\s+)?(?:a\s+|an\s+|the\s+)?{escapedPhrase}\b",
            query,
        )
    )
    preferredCount = len(
        re.findall(
            rf"\b(?:{'|'.join(PREFERENCE_PREFIXES)})\s+"
            rf"(?:a\s+|an\s+|the\s+)?{escapedPhrase}\b",
            query,
        )
    )
    mentionCount = len(re.findall(rf"\b{escapedPhrase}\b", query))
    values: set[str] = set()
    if negativeCount:
        values.add("excluded")
    if preferredCount:
        values.add("preferred")
    if mentionCount > negativeCount + preferredCount:
        values.add("required")
    return values


def parseQuery(query: str) -> ParsedPreferences:
    """Extract only explicitly supported preferences from a query."""
    loweredQuery = query.casefold()
    locationStrengths = {
        area: _phraseStrengths(loweredQuery, area.casefold()) for area in SUPPORTED_AREAS
    }
    location = next(
        (
            area
            for area, values in locationStrengths.items()
            if "required" in values or "preferred" in values
        ),
        None,
    )
    excludedLocations = sorted(
        area for area, values in locationStrengths.items() if "excluded" in values
    )
    priceStrengths = {
        level: _phraseStrengths(loweredQuery, level) for level in ("cheap", "moderate", "expensive")
    }
    priceLevel = next(
        (
            level
            for level, values in priceStrengths.items()
            if "required" in values or "preferred" in values
        ),
        None,
    )
    excludedPriceLevels = sorted(
        level for level, values in priceStrengths.items() if "excluded" in values
    )
    strengths: dict[str, set[str]] = {}
    for feature, phrases in FEATURE_PHRASES.items():
        strengths[feature] = set().union(
            *(_phraseStrengths(loweredQuery, phrase) for phrase in phrases)
        )
    preferredFeatures = sorted(
        feature for feature, values in strengths.items() if "preferred" in values
    )
    excludedFeatures = sorted(
        feature for feature, values in strengths.items() if "excluded" in values
    )
    contradictions = sorted(
        feature
        for feature, values in strengths.items()
        if "excluded" in values and ("required" in values or "preferred" in values)
    )
    if location and location in excludedLocations:
        contradictions.append("location")
    if priceLevel and priceLevel in excludedPriceLevels:
        contradictions.append("priceLevel")

    def isRequired(feature: str) -> bool:
        return "required" in strengths[feature] and feature not in contradictions

    groupMatch = GROUP_SIZE_PATTERN.search(loweredQuery)
    parsedGroupSize = (
        int(next(value for value in groupMatch.groups() if value is not None))
        if groupMatch
        else None
    )
    groupSize = parsedGroupSize if parsedGroupSize and parsedGroupSize <= 50 else None
    noiseStrengths = {level: _phraseStrengths(loweredQuery, level) for level in ("quiet", "lively")}
    for level, values in noiseStrengths.items():
        if "excluded" in values:
            excludedFeatures.append(f"{level}Atmosphere")
        if "preferred" in values:
            preferredFeatures.append(f"{level}Atmosphere")
    positiveNoise = [level for level, values in noiseStrengths.items() if "required" in values]
    if len(positiveNoise) > 1 or any(
        "required" in values and "excluded" in values for values in noiseStrengths.values()
    ):
        contradictions.append("noiseLevel")
    noiseLevel = positiveNoise[0] if len(positiveNoise) == 1 else None

    recognisedPhrases = [
        *(area.casefold() for area in SUPPORTED_AREAS),
        *(phrase for phrases in FEATURE_PHRASES.values() for phrase in phrases),
        *NEGATIVE_PREFIXES,
        *PREFERENCE_PREFIXES,
        "outdoor seating",
        "beer garden",
        "cheap",
        "moderate",
        "expensive",
        "food",
        "sports",
        "football",
        "quiet",
        "lively",
        "group",
    ]
    remainingText = loweredQuery
    remainingText = GROUP_SIZE_PATTERN.sub(" ", remainingText)
    for phrase in sorted(recognisedPhrases, key=len, reverse=True):
        remainingText = re.sub(rf"\b{re.escape(phrase)}\b", " ", remainingText)
    unparsedTerms = [
        term for term in re.findall(r"[a-z0-9'-]+", remainingText) if term not in FILLER_TERMS
    ]

    return ParsedPreferences(
        location=location,
        excludedLocations=excludedLocations,
        priceLevel=priceLevel,
        excludedPriceLevels=excludedPriceLevels,
        requiresFood=isRequired("food"),
        requiresOutdoorSeating=isRequired("outdoorSeating"),
        excludesOutdoorSeating="outdoorSeating" in excludedFeatures,
        showsSports=isRequired("sports"),
        requiresLiveMusic=isRequired("liveMusic"),
        requiresDj=isRequired("dj"),
        requiresEvents=isRequired("events"),
        requiresDogFriendly=isRequired("dogFriendly"),
        requiresWheelchairAccess=isRequired("wheelchairAccess"),
        requiresReservations=isRequired("reservations"),
        suitableForGroups=isRequired("groups") or groupSize is not None,
        requiresHappyHour=isRequired("happyHour"),
        requiresSundayRoast=isRequired("sundayRoast"),
        requiresVeganOptions=isRequired("veganOptions"),
        requiresQuizNight=isRequired("quizNight"),
        requiresAccessibleToilet=isRequired("accessibleToilet"),
        groupSize=groupSize,
        noiseLevel=noiseLevel,
        preferredFeatures=sorted(set(preferredFeatures)),
        excludedFeatures=sorted(set(excludedFeatures)),
        contradictions=sorted(set(contradictions)),
        unparsedTerms=unparsedTerms,
    )
