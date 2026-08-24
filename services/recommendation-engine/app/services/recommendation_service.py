"""Orchestrate parsing, candidate retrieval, and ranking."""

from uuid import uuid4

from app.models.account import VenueRating
from app.models.venue import Venue
from app.parsing.ollama_intent_parser import IntentParser
from app.parsing.query_parser import PARSER_VERSION, parseQuery
from app.preference_features import FEATURE_ATTRIBUTES, featureValue
from app.rag.venue_index import HybridRagRetriever
from app.ranking.venue_ranker import RANKING_VERSION, rankVenues
from app.repositories.venue_repository import VenueRepository
from app.retrieval.candidate_retriever import retrieveCandidates
from app.schemas.recommendation import (
    ParsedPreferences,
    RecommendationRequest,
    RecommendationResponse,
    VenueRecommendation,
)


class RecommendationService:
    """Coordinate the deterministic recommendation pipeline."""

    def __init__(
        self,
        venueRepository: VenueRepository,
        ragRetriever: HybridRagRetriever | None = None,
        intentParser: IntentParser | None = None,
        datasetVersion: str = "unversioned",
        indexVersion: str | None = None,
    ) -> None:
        self.venueRepository = venueRepository
        self.ragRetriever = ragRetriever
        self.intentParser = intentParser
        self.datasetVersion = datasetVersion
        self.indexVersion = indexVersion

    def recommend(
        self, request: RecommendationRequest, userRatings: list[VenueRating] | None = None
    ) -> RecommendationResponse:
        """Return ranked recommendations for a validated request."""
        preferences = parseQuery(request.query)
        if self.intentParser is not None:
            preferences = self.intentParser.enhance(request.query, preferences)
        venues = self.venueRepository.listVenues()
        candidates = retrieveCandidates(venues, preferences)
        recommendations = rankVenues(candidates, preferences, len(candidates))
        activePreferences, ignoredPreferences, warnings = _describePreferences(venues, preferences)
        noResultReasons = _describeNoResults(venues, preferences) if not candidates else []
        retrievalMode = "deterministic"
        if self.ragRetriever is not None and recommendations:
            hasStructuredPreferences = any(
                (
                    preferences.location,
                    preferences.priceLevel,
                    preferences.requiresFood,
                    preferences.requiresOutdoorSeating,
                    preferences.excludesOutdoorSeating,
                    preferences.showsSports,
                    preferences.requiresLiveMusic,
                    preferences.requiresDj,
                    preferences.requiresEvents,
                    preferences.requiresDogFriendly,
                    preferences.requiresWheelchairAccess,
                    preferences.requiresReservations,
                    preferences.suitableForGroups,
                    preferences.requiresHappyHour,
                    preferences.requiresSundayRoast,
                    preferences.requiresVeganOptions,
                    preferences.requiresQuizNight,
                    preferences.requiresAccessibleToilet,
                    preferences.noiseLevel,
                )
            )
            hasDeterministicSignal = any(
                recommendation.score > 0 for recommendation in recommendations
            )
            semanticWeight = 0.25 if hasStructuredPreferences and hasDeterministicSignal else 1.0
            evidenceByVenue = self.ragRetriever.retrieve(
                request.query,
                (venue.venueId for venue in candidates),
                preferredAttributes=_evidenceAttributes(preferences),
            )
            recommendations = [
                recommendation.model_copy(
                    update={
                        "score": round(
                            (1.0 - semanticWeight) * recommendation.score
                            + semanticWeight
                            * evidenceByVenue[recommendation.venueId].semanticScore,
                            3,
                        ),
                        "semanticScore": evidenceByVenue[recommendation.venueId].semanticScore,
                        "evidence": evidenceByVenue[recommendation.venueId].texts,
                        "evidenceSources": evidenceByVenue[recommendation.venueId].sources,
                        "evidenceSourceUrls": evidenceByVenue[recommendation.venueId].sourceUrls,
                        "scoreBreakdown": {
                            "structured": round((1.0 - semanticWeight) * recommendation.score, 3),
                            "semantic": round(
                                semanticWeight
                                * evidenceByVenue[recommendation.venueId].semanticScore,
                                3,
                            ),
                        },
                    }
                )
                for recommendation in recommendations
                if recommendation.venueId in evidenceByVenue
            ]
            recommendations.sort(
                key=lambda recommendation: (-recommendation.score, recommendation.name)
            )
            retrievalMode = "hybrid_rag"
        recommendations = _personalise(recommendations, venues, userRatings or [])
        totalAvailable = len(recommendations)
        pageEnd = request.offset + request.limit
        return RecommendationResponse(
            query=request.query,
            parsedPreferences=preferences,
            recommendations=recommendations[request.offset : pageEnd],
            offset=request.offset,
            limit=request.limit,
            totalAvailable=totalAvailable,
            hasMore=pageEnd < totalAvailable,
            retrievalMode=retrievalMode,
            requestId=str(uuid4()),
            parserVersion=(
                f"{PARSER_VERSION}+{self.intentParser.version}"
                if self.intentParser is not None
                else PARSER_VERSION
            ),
            rankingVersion=RANKING_VERSION,
            datasetVersion=self.datasetVersion,
            indexVersion=self.indexVersion,
            activePreferences=activePreferences,
            ignoredPreferences=ignoredPreferences,
            warnings=warnings,
            noResultReasons=noResultReasons,
            personalised=any(item.personalReason is not None for item in recommendations),
        )


def _personalise(
    recommendations: list[VenueRecommendation],
    venues: list[Venue],
    userRatings: list[VenueRating],
) -> list[VenueRecommendation]:
    """Add a cautious vibe boost using only pubs the user rated four or five stars."""
    ratingsByVenue = {rating.venueId: rating for rating in userRatings}
    venuesById = {venue.venueId: venue for venue in venues}
    liked = [
        (venuesById[rating.venueId], rating.rating)
        for rating in userRatings
        if rating.rating is not None
        and rating.rating >= 4
        and rating.venueId in venuesById
    ]
    personalised: list[VenueRecommendation] = []
    for recommendation in recommendations:
        ownRating = ratingsByVenue.get(recommendation.venueId)
        bestSimilarity = 0.0
        bestName: str | None = None
        bestRating = 0
        candidate = venuesById[recommendation.venueId]
        for likedVenue, likedRating in liked:
            if likedVenue.venueId == candidate.venueId:
                continue
            similarity = _vibeSimilarity(candidate, likedVenue)
            if similarity > bestSimilarity:
                bestSimilarity = similarity
                bestName = likedVenue.name
                bestRating = likedRating
        combinedScore = recommendation.score
        reason = None
        # Only claim a shared vibe when the verified-trait similarity is exceptional.
        if bestName is not None and bestSimilarity >= 0.95:
            personalSignal = bestSimilarity * (bestRating / 5.0)
            combinedScore = 0.85 * recommendation.score + 0.15 * personalSignal
            reason = f"Similar vibe to {bestName}, which you rated highly"
        else:
            bestName = None
        personalised.append(
            recommendation.model_copy(
                update={
                    "score": round(min(1.0, combinedScore), 3),
                    "beenHere": ownRating.beenHere if ownRating else False,
                    "userRating": ownRating.rating if ownRating else None,
                    "personalScore": round(bestSimilarity, 3) if bestName else None,
                    "personalReason": reason,
                }
            )
        )
    return sorted(personalised, key=lambda item: (-item.score, item.name))


def _vibeSimilarity(first: Venue, second: Venue) -> float:
    """Compare known positive venue traits without treating unknown as false."""
    attributes = tuple(FEATURE_ATTRIBUTES.values()) + ("area", "priceLevel", "noiseLevel")
    firstTraits = {
        f"{attribute}:{getattr(first, attribute)}"
        for attribute in attributes
        if getattr(first, attribute, None) not in (None, False)
    }
    secondTraits = {
        f"{attribute}:{getattr(second, attribute)}"
        for attribute in attributes
        if getattr(second, attribute, None) not in (None, False)
    }
    union = firstTraits | secondTraits
    return len(firstTraits & secondTraits) / len(union) if union else 0.0


def _evidenceAttributes(preferences: ParsedPreferences) -> set[str]:
    """Translate parsed requirements into canonical evidence attributes."""
    flags = {
        "servesFood": preferences.requiresFood,
        "hasOutdoorSeating": (
            preferences.requiresOutdoorSeating or preferences.excludesOutdoorSeating
        ),
        "showsSports": preferences.showsSports,
        "hasLiveMusic": preferences.requiresLiveMusic,
        "hasDj": preferences.requiresDj,
        "hostsEvents": preferences.requiresEvents,
        "dogFriendly": preferences.requiresDogFriendly,
        "wheelchairAccessible": preferences.requiresWheelchairAccess,
        "acceptsReservations": preferences.requiresReservations,
        "suitableForGroups": preferences.suitableForGroups,
        "hasHappyHour": preferences.requiresHappyHour,
        "servesSundayRoast": preferences.requiresSundayRoast,
        "hasVeganOptions": preferences.requiresVeganOptions,
        "hostsQuiz": preferences.requiresQuizNight,
        "hasAccessibleToilet": preferences.requiresAccessibleToilet,
    }
    attributes = {attribute for attribute, enabled in flags.items() if enabled}
    attributes.update(
        FEATURE_ATTRIBUTES[feature]
        for feature in preferences.excludedFeatures
        if feature in FEATURE_ATTRIBUTES
    )
    return attributes


def _describePreferences(
    venues: list[Venue], preferences: ParsedPreferences
) -> tuple[list[str], list[str], list[str]]:
    """Summarise which parsed signals can influence the current result set."""
    parsed = preferences
    active: list[str] = []
    ignored: list[str] = []
    warnings: list[str] = []

    for name in ("location", "priceLevel", "noiseLevel"):
        value = getattr(parsed, name)
        if value is not None:
            active.append(f"{name}:{value}")
    requiredFlags = {
        "food": "requiresFood",
        "outdoorSeating": "requiresOutdoorSeating",
        "sports": "showsSports",
        "liveMusic": "requiresLiveMusic",
        "dj": "requiresDj",
        "events": "requiresEvents",
        "dogFriendly": "requiresDogFriendly",
        "wheelchairAccess": "requiresWheelchairAccess",
        "reservations": "requiresReservations",
        "happyHour": "requiresHappyHour",
        "sundayRoast": "requiresSundayRoast",
        "veganOptions": "requiresVeganOptions",
        "quizNight": "requiresQuizNight",
        "accessibleToilet": "requiresAccessibleToilet",
    }
    active.extend(feature for feature, flag in requiredFlags.items() if getattr(parsed, flag))
    active.extend(f"prefer:{feature}" for feature in parsed.preferredFeatures)
    active.extend(f"exclude:{feature}" for feature in parsed.excludedFeatures)
    active.extend(f"exclude:location:{area}" for area in parsed.excludedLocations)
    active.extend(f"exclude:priceLevel:{level}" for level in parsed.excludedPriceLevels)
    if parsed.suitableForGroups:
        active.append("groupSuitability")

    # A parsed soft preference cannot affect ranking when every value is unknown.
    if parsed.priceLevel and not any(venue.priceLevel is not None for venue in venues):
        ignored.append(f"priceLevel:{parsed.priceLevel}")
    if parsed.noiseLevel and not any(venue.noiseLevel is not None for venue in venues):
        ignored.append(f"noiseLevel:{parsed.noiseLevel}")
    for feature in parsed.preferredFeatures:
        if not any(featureValue(venue, feature) is not None for venue in venues):
            ignored.append(f"prefer:{feature}")
    if parsed.unparsedTerms:
        ignored.extend(f"unparsed:{term}" for term in parsed.unparsedTerms)
        warnings.append("Some query terms were not understood: " + ", ".join(parsed.unparsedTerms))
    if parsed.contradictions:
        warnings.append(
            "The query contains conflicting requirements: " + ", ".join(parsed.contradictions)
        )
    if ignored:
        warnings.append("Some preferences could not affect ranking because data was unavailable.")
    return sorted(set(active) - set(ignored)), sorted(set(ignored)), warnings


def _describeNoResults(venues: list[Venue], preferences: ParsedPreferences) -> list[str]:
    """Return factual reasons for an empty candidate set."""
    parsed = preferences
    if parsed.contradictions:
        return ["Conflicting requirements must be resolved before matching venues."]

    reasons: list[str] = []
    if parsed.location and not any(venue.area == parsed.location for venue in venues):
        reasons.append(f"No venues are available in {parsed.location}.")
    requiredFlags = {
        "food": "requiresFood",
        "outdoorSeating": "requiresOutdoorSeating",
        "sports": "showsSports",
        "liveMusic": "requiresLiveMusic",
        "dj": "requiresDj",
        "events": "requiresEvents",
        "dogFriendly": "requiresDogFriendly",
        "wheelchairAccess": "requiresWheelchairAccess",
        "reservations": "requiresReservations",
        "happyHour": "requiresHappyHour",
        "sundayRoast": "requiresSundayRoast",
        "veganOptions": "requiresVeganOptions",
        "quizNight": "requiresQuizNight",
        "accessibleToilet": "requiresAccessibleToilet",
    }
    for feature, flag in requiredFlags.items():
        if getattr(parsed, flag) and not any(
            featureValue(venue, feature) is True for venue in venues
        ):
            reasons.append(f"No venues have verified {feature} data matching the request.")
    for feature in parsed.excludedFeatures:
        if not any(featureValue(venue, feature) is False for venue in venues):
            reasons.append(f"No venues have verified evidence excluding {feature}.")
    for level in parsed.excludedPriceLevels:
        if not any(venue.priceLevel is not None and venue.priceLevel != level for venue in venues):
            reasons.append(f"No venues have verified prices outside {level}.")
    if not reasons:
        reasons.append("No venue satisfies all verified requirements together.")
    return reasons
