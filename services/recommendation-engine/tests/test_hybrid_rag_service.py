"""Integration tests for hybrid RAG ranking."""

from datetime import UTC, datetime

from app.models.account import VenueRating
from app.models.venue import Venue
from app.rag.venue_index import EvidenceChunk, HybridRagRetriever, VenueRagIndex
from app.schemas.recommendation import RecommendationRequest
from app.services.recommendation_service import RecommendationService


class Repository:
    def __init__(self, venues: list[Venue]) -> None:
        self.venues = venues

    def listVenues(self) -> list[Venue]:
        return self.venues


class QueryProvider:
    modelName = "fake"

    def embedPassages(self, passages: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embedQuery(self, query: str) -> list[float]:
        return [1.0, 0.0]


def venue(venueId: str, rating: float) -> Venue:
    return Venue(
        venueId=venueId,
        name=venueId,
        latitude=51.5,
        longitude=-0.13,
        area="Westminster",
        servesFood=True,
        rating=rating,
        popularityScore=0.5,
        tags=[],
    )


def test_hybrid_service_adds_evidence_and_can_rerank() -> None:
    venues = [venue("quality", 5.0), venue("semantic", 3.0)]
    index = VenueRagIndex(
        modelName="fake",
        venueDataSha256="unused",
        venueCount=2,
        chunks=[
            EvidenceChunk(
                venueId="quality",
                text="Generic pub",
                source="FSQ",
                embedding=[0.0, 1.0],
            ),
            EvidenceChunk(
                venueId="semantic",
                text="A hidden cosy place",
                source="Official venue website",
                sourceUrl="https://example.com",
                embedding=[1.0, 0.0],
            ),
        ],
    )
    service = RecommendationService(
        Repository(venues), HybridRagRetriever(index, QueryProvider())
    )

    response = service.recommend(
        RecommendationRequest(query="hidden cosy pub", limit=2)
    )

    assert response.retrievalMode == "hybrid_rag"
    assert response.recommendations[0].venueId == "semantic"
    assert abs(
        response.recommendations[0].score
        - (response.recommendations[0].semanticScore or 0.0)
    ) < 0.001
    assert response.recommendations[0].score > 0.9
    assert response.recommendations[0].semanticScore == 1.0
    assert response.recommendations[0].evidence == ["A hidden cosy place"]
    assert response.recommendations[0].evidenceSourceUrls == ["https://example.com"]


def test_service_without_index_remains_deterministic() -> None:
    service = RecommendationService(Repository([venue("pub", 4.0)]))
    response = service.recommend(RecommendationRequest(query="pub", limit=1))

    assert response.retrievalMode == "deterministic"
    assert response.recommendations[0].semanticScore is None


def test_semantic_score_is_not_diluted_when_structured_data_is_unknown() -> None:
    candidate = venue("lively", 0.0)
    candidate.rating = None
    candidate.popularityScore = None
    index = VenueRagIndex(
        modelName="fake",
        venueDataSha256="unused",
        venueCount=1,
        chunks=[
            EvidenceChunk(
                venueId="lively",
                text="A lively pub for birthday celebrations.",
                source="Official venue website",
                embedding=[1.0, 0.0],
            )
        ],
    )
    service = RecommendationService(
        Repository([candidate]), HybridRagRetriever(index, QueryProvider())
    )

    response = service.recommend(
        RecommendationRequest(query="lively birthday celebration", limit=1)
    )

    assert abs(
        response.recommendations[0].score
        - (response.recommendations[0].semanticScore or 0.0)
    ) < 0.001
    assert response.recommendations[0].score > 0.9


def test_service_prioritises_evidence_for_parsed_feature() -> None:
    candidate = venue("sport", 4.0)
    candidate.showsSports = True
    index = VenueRagIndex(
        modelName="fake",
        venueDataSha256="unused",
        venueCount=1,
        chunks=[
            EvidenceChunk(
                venueId="sport",
                text="Generic central pub.",
                source="source",
                attributes=["name", "area"],
                embedding=[1.0, 0.0],
            ),
            EvidenceChunk(
                venueId="sport",
                text="This pub shows live sport.",
                source="official",
                attributes=["showsSports"],
                embedding=[0.9, 0.1],
            ),
        ],
    )
    service = RecommendationService(
        Repository([candidate]), HybridRagRetriever(index, QueryProvider())
    )

    response = service.recommend(RecommendationRequest(query="pub showing football"))

    assert response.recommendations[0].evidence == ["This pub shows live sport."]


def test_personalisation_only_claims_at_least_95_percent_vibe_similarity() -> None:
    liked = venue("liked", 4.0)
    liked.hasLiveMusic = True
    near = liked.model_copy(update={"venueId": "near", "name": "near"})
    loose = liked.model_copy(
        update={"venueId": "loose", "name": "loose", "hasLiveMusic": False}
    )
    rating = VenueRating(
        userId="user",
        venueId="liked",
        rating=5,
        visitedAt=datetime.now(UTC),
        updatedAt=datetime.now(UTC),
    )
    response = RecommendationService(Repository([liked, near, loose])).recommend(
        RecommendationRequest(query="pub", limit=3), [rating]
    )
    byId = {item.venueId: item for item in response.recommendations}

    assert byId["near"].personalScore == 1.0
    assert byId["near"].personalReason is not None
    assert byId["loose"].personalReason is None
    assert response.personalised is True
