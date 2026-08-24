"""Tests for versioned grounded-retrieval evaluation."""

import argparse
import json
import sys
from pathlib import Path

import pytest

from app.models.venue import Venue
from app.parsing.query_parser import parseQuery
from app.rag import evaluate_retrieval
from app.rag.evaluate_retrieval import RetrievalEvaluationCase, evaluateCases, parseCutoffs
from app.rag.venue_index import EvidenceChunk, HybridRagRetriever, VenueRagIndex
from app.schemas.recommendation import ParsedPreferences


class Provider:
    modelName = "fake"

    def embedPassages(self, passages: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embedQuery(self, query: str) -> list[float]:
        return [1.0, 0.0]


def venue(venueId: str, sports: bool) -> Venue:
    return Venue(
        venueId=venueId,
        name=venueId,
        latitude=51.5,
        longitude=-0.1,
        area="Westminster",
        showsSports=sports,
        tags=[],
    )


def test_evaluation_measures_relevance_and_grounded_terms() -> None:
    venues = [venue("sport", True), venue("other", False)]
    index = VenueRagIndex(
        modelName="fake",
        venueDataSha256="unused",
        venueCount=2,
        chunks=[
            EvidenceChunk(
                venueId="sport",
                text="This pub shows football and live sport.",
                source="official",
                embedding=[1.0, 0.0],
            ),
            EvidenceChunk(
                venueId="other",
                text="A generic venue.",
                source="source",
                embedding=[0.0, 1.0],
            ),
        ],
    )
    cases = [
        RetrievalEvaluationCase(
            name="sport",
            query="football",
            topK=1,
            requiredAttributes=["showsSports"],
            expectedEvidenceTermGroups=[["football", "sport"]],
        )
    ]

    report = evaluateCases(cases, venues, HybridRagRetriever(index, Provider()))

    assert report["aggregate"] == {
        "meanPrecisionAtK": 1.0,
        "meanRecallAtK": 1.0,
        "hitRateAtK": 1.0,
        "meanReciprocalRank": 1.0,
        "meanEvidenceTermCoverage": 1.0,
        "meanNdcgAtK": 1.0,
        "meanReturnedResultsAtK": 1.0,
        "ragasIdContextPrecision": None,
        "ragasIdContextRecall": None,
        "ragasEvaluatedCaseCount": 0,
    }
    assert report["cases"][0]["topResults"][0]["venueId"] == "sport"
    assert report["cases"][0]["candidateVenueCount"] == 1
    assert report["cutoffMetrics"]["1"] == {
        "precision": 1.0,
        "recall": 1.0,
        "capacityAdjustedRecall": 1.0,
    }
    assert report["cutoffMetrics"]["20"]["capacityAdjustedRecall"] == 1.0


def test_evaluation_can_score_exact_evidence_ids() -> None:
    venues = [venue("sport", True), venue("other", False)]
    index = VenueRagIndex(
        modelName="fake",
        venueDataSha256="unused",
        venueCount=2,
        chunks=[
            EvidenceChunk(
                venueId="sport",
                text="This pub shows football and live sport.",
                source="official",
                attributes=["showsSports"],
                embedding=[1.0, 0.0],
            ),
            EvidenceChunk(
                venueId="sport",
                text="This pub has a central address.",
                source="official",
                attributes=["address"],
                embedding=[0.9, 0.1],
            ),
        ],
    )
    calls: list[tuple[list[str], list[str]]] = []

    class Scorer:
        def score(
            self, retrievedIds: list[str], referenceIds: list[str]
        ) -> tuple[float, float]:
            calls.append((retrievedIds, referenceIds))
            return 0.5, 1.0

    report = evaluateCases(
        [
            RetrievalEvaluationCase(
                name="sport-evidence",
                query="football",
                topK=1,
                requiredAttributes=["showsSports"],
            )
        ],
        venues,
        HybridRagRetriever(index, Provider()),
        evidenceScorer=Scorer(),
    )

    assert len(calls) == 1
    assert len(calls[0][0]) == 1
    assert len(calls[0][1]) == 1
    assert report["aggregate"]["ragasIdContextPrecision"] == 0.5
    assert report["aggregate"]["ragasIdContextRecall"] == 1.0
    assert report["aggregate"]["ragasEvaluatedCaseCount"] == 1
    assert report["cases"][0]["ragasEvidence"]["referenceContextCount"] == 1


def test_evaluation_reports_traditional_and_capacity_adjusted_recall() -> None:
    venues = [venue(f"sport-{index}", True) for index in range(10)]
    index = VenueRagIndex(
        modelName="fake",
        venueDataSha256="unused",
        venueCount=10,
        chunks=[
            EvidenceChunk(
                venueId=item.venueId,
                text=f"{item.name} shows live sport.",
                source="official",
                embedding=[1.0, 0.0],
            )
            for item in venues
        ],
    )
    case = RetrievalEvaluationCase(
        name="broad-sports",
        query="live sport",
        requiredAttributes=["showsSports"],
    )

    report = evaluateCases(
        [case],
        venues,
        HybridRagRetriever(index, Provider()),
        cutoffs=(1, 3, 5, 10),
    )

    assert report["cutoffMetrics"]["5"]["recall"] == 0.5
    assert report["cutoffMetrics"]["5"]["capacityAdjustedRecall"] == 1.0
    assert report["cutoffMetrics"]["10"]["recall"] == 1.0


@pytest.mark.parametrize("value", ["", "0,5", "one,three"])
def test_rejects_invalid_evaluation_cutoffs(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parseCutoffs(value)


def test_evaluation_case_rejects_unknown_attributes() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        RetrievalEvaluationCase(name="bad", query="bad", requiredAttributes=["inventedFeature"])
    with pytest.raises(ValueError, match="grades"):
        RetrievalEvaluationCase(name="bad-grade", query="bad", relevanceJudgments={"venue": 4})
    with pytest.raises(ValueError, match="cannot be combined"):
        RetrievalEvaluationCase(
            name="bad-empty",
            query="bad",
            requiredAttributes=["showsSports"],
            expectNoResults=True,
        )


def test_evaluation_uses_graded_venue_judgments() -> None:
    venues = [venue("best", False), venue("okay", False)]
    index = VenueRagIndex(
        modelName="fake",
        venueDataSha256="unused",
        venueCount=2,
        chunks=[
            EvidenceChunk(
                venueId="best",
                text="Perfect historic character.",
                source="official",
                embedding=[1.0, 0.0],
            ),
            EvidenceChunk(
                venueId="okay",
                text="Some character.",
                source="official",
                embedding=[0.5, 0.5],
            ),
        ],
    )
    case = RetrievalEvaluationCase(
        name="graded",
        query="historic character",
        topK=2,
        relevanceJudgments={"best": 3, "okay": 1},
    )

    report = evaluateCases([case], venues, HybridRagRetriever(index, Provider()))

    assert report["aggregate"]["meanNdcgAtK"] == 1.0
    assert report["cases"][0]["topResults"][0]["relevanceGrade"] == 3


def test_evaluation_filters_verified_requirements_without_padding() -> None:
    venues = [venue("sport", True), venue("other", False)]
    index = VenueRagIndex(
        modelName="fake",
        venueDataSha256="unused",
        venueCount=2,
        chunks=[
            EvidenceChunk(
                venueId="sport",
                text="This pub shows live sport.",
                source="official",
                embedding=[1.0, 0.0],
            ),
            EvidenceChunk(
                venueId="other",
                text="This generic venue has a deceptively strong embedding.",
                source="official",
                embedding=[1.0, 0.0],
            ),
        ],
    )
    case = RetrievalEvaluationCase(
        name="sport-no-padding",
        query="somewhere to watch the match",
        topK=5,
        requiredAttributes=["showsSports"],
    )

    report = evaluateCases([case], venues, HybridRagRetriever(index, Provider()))

    assert report["aggregate"]["meanPrecisionAtK"] == 1.0
    assert report["aggregate"]["meanReturnedResultsAtK"] == 1.0
    assert report["cases"][0]["candidateVenueCount"] == 1
    assert [result["venueId"] for result in report["cases"][0]["topResults"]] == ["sport"]


def test_evaluation_scores_verified_negative_requirements() -> None:
    venues = [venue("sport", True), venue("no-sport", False)]
    index = VenueRagIndex(
        modelName="fake",
        venueDataSha256="unused",
        venueCount=2,
        chunks=[
            EvidenceChunk(
                venueId="sport",
                text="Football shown here.",
                source="official",
                embedding=[1.0, 0.0],
            ),
            EvidenceChunk(
                venueId="no-sport",
                text="A quiet local pub.",
                source="official",
                embedding=[0.0, 1.0],
            ),
        ],
    )
    case = RetrievalEvaluationCase(
        name="no-sport",
        query="pub with no football",
        excludedAttributes=["showsSports"],
    )

    report = evaluateCases([case], venues, HybridRagRetriever(index, Provider()))

    assert report["aggregate"]["meanPrecisionAtK"] == 1.0
    assert report["cases"][0]["excludedAttributes"] == ["showsSports"]
    assert report["cases"][0]["topResults"][0]["venueId"] == "no-sport"


def test_evaluation_scores_expected_empty_contradictions() -> None:
    venues = [venue("sport", True)]
    index = VenueRagIndex(
        modelName="fake",
        venueDataSha256="unused",
        venueCount=1,
        chunks=[
            EvidenceChunk(
                venueId="sport",
                text="Football shown here.",
                source="official",
                embedding=[1.0, 0.0],
            )
        ],
    )
    case = RetrievalEvaluationCase(
        name="contradiction",
        query="football but no football",
        expectNoResults=True,
    )

    report = evaluateCases([case], venues, HybridRagRetriever(index, Provider()))

    assert report["aggregate"]["meanPrecisionAtK"] == 1.0
    assert report["aggregate"]["hitRateAtK"] == 1.0
    assert report["cases"][0]["expectedEmpty"] is True
    assert report["cases"][0]["topResults"] == []


def test_cli_can_evaluate_with_local_ollama_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the optional local-model benchmark path executable and versioned."""
    venues = [venue("sport", True)]
    index = VenueRagIndex(
        modelName="fake",
        venueDataSha256="unused",
        venueCount=1,
        chunks=[
            EvidenceChunk(
                venueId="sport",
                text="Live football shown here.",
                source="official",
                embedding=[1.0, 0.0],
            )
        ],
    )
    casesPath = tmp_path / "cases.json"
    reportPath = tmp_path / "report.json"
    casesPath.write_text(
        json.dumps(
            [
                {
                    "name": "sport",
                    "query": "football",
                    "requiredAttributes": ["showsSports"],
                }
            ]
        ),
        encoding="utf-8",
    )

    class Repository:
        def __init__(self, path: Path) -> None:
            pass

        def listVenues(self) -> list[Venue]:
            return venues

    class Parser:
        version = "test-ollama"

        def __init__(self, **kwargs: object) -> None:
            self.closed = False

        def enhance(self, query: str, parsed: ParsedPreferences) -> ParsedPreferences:
            return parseQuery(query)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(evaluate_retrieval, "JsonVenueRepository", Repository)
    monkeypatch.setattr(evaluate_retrieval, "loadIndex", lambda *args: index)
    monkeypatch.setattr(evaluate_retrieval, "FastEmbedProvider", lambda *args: Provider())
    monkeypatch.setattr(evaluate_retrieval, "SemanticIntentParser", lambda provider: Parser())
    monkeypatch.setattr(evaluate_retrieval, "OllamaIntentParser", Parser)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_retrieval",
            str(tmp_path / "venues.json"),
            str(tmp_path / "index.json"),
            str(casesPath),
            str(reportPath),
            "--ollama-model",
            "test-model",
        ],
    )

    assert evaluate_retrieval.main() == 0
    report = json.loads(reportPath.read_text(encoding="utf-8"))
    assert report["intentParserVersion"] == "test-ollama"
    assert report["aggregate"]["meanPrecisionAtK"] == 1.0
