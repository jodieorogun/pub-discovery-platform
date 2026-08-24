"""Evaluate grounded venue retrieval against versioned query expectations."""

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

from app.models.venue import Venue
from app.parsing.ollama_intent_parser import (
    DEFAULT_OLLAMA_URL,
    IntentParser,
    OllamaIntentParser,
)
from app.parsing.query_parser import parseQuery
from app.parsing.semantic_intent_parser import SemanticIntentParser
from app.preference_features import FEATURE_ATTRIBUTES
from app.rag.ragas_evidence import EvidenceIdScorer, RagasIdEvidenceScorer
from app.rag.venue_index import (
    DEFAULT_DENSE_WEIGHT,
    FastEmbedProvider,
    HybridRagRetriever,
    evidenceChunkId,
    loadIndex,
)
from app.repositories.json_venue_repository import JsonVenueRepository
from app.retrieval.candidate_retriever import retrieveCandidates

ALLOWED_ATTRIBUTES = set(FEATURE_ATTRIBUTES.values())
DEFAULT_EVALUATION_CUTOFFS = (1, 3, 5, 10, 20)


class RetrievalEvaluationCase(BaseModel):
    """One query with structured relevance or grounded-text expectations."""

    name: str
    query: str
    topK: int = Field(default=5, ge=1, le=20)
    requiredAttributes: list[str] = Field(default_factory=list)
    excludedAttributes: list[str] = Field(default_factory=list)
    expectNoResults: bool = False
    expectedEvidenceTermGroups: list[list[str]] = Field(default_factory=list)
    relevanceJudgments: dict[str, int] = Field(default_factory=dict)

    @field_validator("relevanceJudgments")
    @classmethod
    def validateJudgments(cls, value: dict[str, int]) -> dict[str, int]:
        if any(grade < 0 or grade > 3 for grade in value.values()):
            raise ValueError("relevance grades must be between 0 and 3")
        return value

    @field_validator("requiredAttributes", "excludedAttributes")
    @classmethod
    def validateAttributes(cls, value: list[str]) -> list[str]:
        unsupported = set(value) - ALLOWED_ATTRIBUTES
        if unsupported:
            raise ValueError(f"unsupported attributes: {sorted(unsupported)}")
        return value

    @model_validator(mode="after")
    def validateExpectedOutcome(self) -> "RetrievalEvaluationCase":
        if self.expectNoResults and (
            self.requiredAttributes or self.excludedAttributes or self.relevanceJudgments
        ):
            raise ValueError("expectNoResults cannot be combined with relevance criteria")
        return self


def _isRelevant(venue: Venue, requiredAttributes: list[str], excludedAttributes: list[str]) -> bool:
    """Match only explicitly verified positive and negative venue facts."""
    hasCriteria = bool(requiredAttributes or excludedAttributes)
    return (
        hasCriteria
        and all(getattr(venue, attribute) is True for attribute in requiredAttributes)
        and all(getattr(venue, attribute) is False for attribute in excludedAttributes)
    )


def _ndcgAtK(rankedGrades: list[int], idealGrades: list[int], topK: int) -> float:
    """Calculate normalised discounted cumulative gain for graded judgments."""

    def gain(grades: list[int]) -> float:
        return float(
            sum((2**grade - 1) / math.log2(rank + 2) for rank, grade in enumerate(grades[:topK]))
        )

    idealGain = gain(sorted(idealGrades, reverse=True))
    return gain(rankedGrades) / idealGain if idealGain else 0.0


def _scoresAtCutoff(
    rankedVenueIds: list[str],
    relevantIds: set[str],
    expectedEmpty: bool,
    cutoff: int,
) -> tuple[float, float, float]:
    """Return precision, traditional recall, and capacity-adjusted recall at X."""
    topIds = rankedVenueIds[:cutoff]
    if expectedEmpty:
        correctAbstention = float(not topIds)
        return correctAbstention, correctAbstention, correctAbstention
    relevantReturned = sum(venueId in relevantIds for venueId in topIds)
    precision = relevantReturned / len(topIds) if topIds else 0.0
    recall = relevantReturned / len(relevantIds) if relevantIds else 0.0
    # A five-result page cannot retrieve more than five relevant venues. This
    # version measures whether the available result slots were used well.
    capacity = min(cutoff, len(relevantIds))
    capacityAdjustedRecall = relevantReturned / capacity if capacity else 0.0
    return precision, recall, capacityAdjustedRecall


def parseCutoffs(value: str) -> tuple[int, ...]:
    """Parse a comma-separated list of unique positive evaluation cutoffs."""
    try:
        cutoffs = tuple(dict.fromkeys(int(part.strip()) for part in value.split(",")))
    except ValueError as error:
        raise argparse.ArgumentTypeError("cutoffs must be comma-separated integers") from error
    if not cutoffs or any(cutoff < 1 for cutoff in cutoffs):
        raise argparse.ArgumentTypeError("cutoffs must be positive integers")
    return cutoffs


def evaluateCases(
    cases: list[RetrievalEvaluationCase],
    venues: list[Venue],
    retriever: HybridRagRetriever,
    intentParser: IntentParser | None = None,
    cutoffs: tuple[int, ...] = DEFAULT_EVALUATION_CUTOFFS,
    evidenceScorer: EvidenceIdScorer | None = None,
) -> dict[str, Any]:
    """Calculate precision, first-relevant rank, and evidence-term coverage."""
    venueById = {venue.venueId: venue for venue in venues}
    caseReports: list[dict[str, Any]] = []
    precisions: list[float] = []
    reciprocalRanks: list[float] = []
    hitValues: list[float] = []
    termCoverages: list[float] = []
    ndcgValues: list[float] = []
    recalls: list[float] = []
    returnedResultCounts: list[float] = []
    scoresByCutoff: dict[int, dict[str, list[float]]] = {
        cutoff: {"precision": [], "recall": [], "capacityAdjustedRecall": []}
        for cutoff in cutoffs
    }
    contextPrecisions: list[float] = []
    contextRecalls: list[float] = []
    for case in cases:
        # Evaluate the same precision-first pipeline used by the API. Relevance
        # labels are never used to select candidates; only the parsed query is.
        parsedPreferences = parseQuery(case.query)
        if intentParser is not None:
            parsedPreferences = intentParser.enhance(case.query, parsedPreferences)
        candidateIds = [venue.venueId for venue in retrieveCandidates(venues, parsedPreferences)]
        targetAttributes = set(case.requiredAttributes + case.excludedAttributes)
        retrieved = retriever.retrieve(
            case.query,
            candidateIds,
            preferredAttributes=targetAttributes or None,
        )
        ranked = sorted(retrieved.items(), key=lambda item: item[1].semanticScore, reverse=True)
        top = ranked[: case.topK]
        structuredRelevantIds = {
            venue.venueId
            for venue in venues
            if _isRelevant(
                venue,
                case.requiredAttributes,
                case.excludedAttributes,
            )
        }
        relevanceGrades = (
            case.relevanceJudgments
            if case.relevanceJudgments
            else {venueId: 3 for venueId in structuredRelevantIds}
        )
        relevantIds = {venueId for venueId, grade in relevanceGrades.items() if grade >= 2}
        expectedEmpty = case.expectNoResults or bool(
            (case.requiredAttributes or case.excludedAttributes) and not structuredRelevantIds
        )
        rankedVenueIds = [venueId for venueId, _ in ranked]
        caseCutoffMetrics: dict[str, dict[str, float]] = {}
        for cutoff in cutoffs:
            cutoffPrecision, cutoffRecall, capacityAdjustedRecall = _scoresAtCutoff(
                rankedVenueIds,
                relevantIds,
                expectedEmpty,
                cutoff,
            )
            scoresByCutoff[cutoff]["precision"].append(cutoffPrecision)
            scoresByCutoff[cutoff]["recall"].append(cutoffRecall)
            scoresByCutoff[cutoff]["capacityAdjustedRecall"].append(capacityAdjustedRecall)
            caseCutoffMetrics[str(cutoff)] = {
                "precision": round(cutoffPrecision, 4),
                "recall": round(cutoffRecall, 4),
                "capacityAdjustedRecall": round(capacityAdjustedRecall, 4),
            }
        relevantRanks = [
            rank for rank, (venueId, _) in enumerate(ranked, start=1) if venueId in relevantIds
        ]
        precision: float | None = None
        reciprocalRank: float | None = None
        hitAtK: bool | None = None
        recall: float | None = None
        if (
            case.requiredAttributes
            or case.excludedAttributes
            or case.relevanceJudgments
            or case.expectNoResults
        ):
            relevantInTop = sum(venueId in relevantIds for venueId, _ in top)
            if expectedEmpty:
                # Correct abstention is a successful outcome, not a retrieval miss.
                precision = float(not top)
                recall = float(not top)
                reciprocalRank = float(not top)
                hitAtK = not top
            else:
                precision = relevantInTop / len(top) if top else 0.0
                recall = relevantInTop / len(relevantIds) if relevantIds else 0.0
                firstRank = min(relevantRanks) if relevantRanks else None
                reciprocalRank = 1.0 / firstRank if firstRank else 0.0
                hitAtK = any(rank <= case.topK for rank in relevantRanks)
            precisions.append(precision)
            recalls.append(recall)
            reciprocalRanks.append(reciprocalRank)
            hitValues.append(float(hitAtK))
        returnedResultCounts.append(float(len(top)))

        ndcg: float | None = None
        if expectedEmpty:
            ndcg = float(not top)
            ndcgValues.append(ndcg)
        elif relevanceGrades:
            ndcg = _ndcgAtK(
                [relevanceGrades.get(venueId, 0) for venueId, _ in ranked],
                list(relevanceGrades.values()),
                case.topK,
            )
            ndcgValues.append(ndcg)

        retrievedText = " ".join(text.casefold() for _, evidence in top for text in evidence.texts)
        matchedGroups = [
            any(term.casefold() in retrievedText for term in group)
            for group in case.expectedEvidenceTermGroups
        ]
        termCoverage: float | None = None
        if matchedGroups:
            termCoverage = sum(matchedGroups) / len(matchedGroups)
            termCoverages.append(termCoverage)
        evidenceMetrics: dict[str, Any] | None = None
        if evidenceScorer is not None and not expectedEmpty:
            # Match the product's top-K scope. Referencing every relevant venue
            # would make recall impossible for a UI that intentionally shows five.
            evaluatedVenueIds = {
                venueId for venueId, _ in top if venueId in relevantIds
            }
            # Do not let complete venue misses disappear from the Ragas mean.
            # A bounded fallback reference makes their context scores zero.
            referenceVenueIds = evaluatedVenueIds or set(
                sorted(relevantIds)[: case.topK]
            )
            referenceChunks = [
                chunk
                for chunk in retriever.referenceChunks(referenceVenueIds)
                if chunk.venueId in referenceVenueIds
                and (
                    bool(targetAttributes.intersection(chunk.attributes))
                    if targetAttributes
                    else any(
                        any(term.casefold() in chunk.text.casefold() for term in group)
                        for group in case.expectedEvidenceTermGroups
                    )
                )
            ]
            referenceIds = [evidenceChunkId(chunk) for chunk in referenceChunks]
            retrievedIds = [chunkId for _, evidence in top for chunkId in evidence.chunkIds]
            if referenceIds:
                contextPrecision, contextRecall = evidenceScorer.score(retrievedIds, referenceIds)
                contextPrecisions.append(contextPrecision)
                contextRecalls.append(contextRecall)
                evidenceMetrics = {
                    "contextPrecision": round(contextPrecision, 4),
                    "contextRecall": round(contextRecall, 4),
                    "retrievedContextCount": len(retrievedIds),
                    "referenceContextCount": len(referenceIds),
                }
        caseReports.append(
            {
                "name": case.name,
                "query": case.query,
                "topK": case.topK,
                "requiredAttributes": case.requiredAttributes,
                "excludedAttributes": case.excludedAttributes,
                "expectNoResults": case.expectNoResults,
                "expectedEmpty": expectedEmpty,
                "parsedPreferences": parsedPreferences.model_dump(),
                "candidateVenueCount": len(candidateIds),
                "returnedResultCount": len(top),
                "relevanceJudgments": case.relevanceJudgments,
                "relevantVenueCount": len(relevantIds),
                "precisionAtK": round(precision, 4) if precision is not None else None,
                "recallAtK": round(recall, 4) if recall is not None else None,
                "hitAtK": hitAtK,
                "reciprocalRank": (
                    round(reciprocalRank, 4) if reciprocalRank is not None else None
                ),
                "evidenceTermCoverage": (
                    round(termCoverage, 4) if termCoverage is not None else None
                ),
                "ndcgAtK": round(ndcg, 4) if ndcg is not None else None,
                "cutoffMetrics": caseCutoffMetrics,
                "matchedEvidenceTermGroups": matchedGroups,
                "ragasEvidence": evidenceMetrics,
                "topResults": [
                    {
                        "venueId": venueId,
                        "venueName": venueById[venueId].name,
                        "semanticScore": evidence.semanticScore,
                        "relevanceGrade": relevanceGrades.get(venueId),
                        "evidence": evidence.texts,
                        "evidenceChunkIds": evidence.chunkIds,
                    }
                    for venueId, evidence in top
                ],
            }
        )
    cutoffMetrics = {
        str(cutoff): {
            metric: round(fmean(values), 4)
            for metric, values in metricValues.items()
        }
        for cutoff, metricValues in scoresByCutoff.items()
    }
    return {
        "generatedAt": datetime.now(UTC).isoformat(),
        "caseCount": len(cases),
        "aggregate": {
            "meanPrecisionAtK": round(fmean(precisions), 4) if precisions else None,
            "meanRecallAtK": round(fmean(recalls), 4) if recalls else None,
            "hitRateAtK": round(fmean(hitValues), 4) if hitValues else None,
            "meanReciprocalRank": (round(fmean(reciprocalRanks), 4) if reciprocalRanks else None),
            "meanEvidenceTermCoverage": (round(fmean(termCoverages), 4) if termCoverages else None),
            "meanNdcgAtK": round(fmean(ndcgValues), 4) if ndcgValues else None,
            "meanReturnedResultsAtK": round(fmean(returnedResultCounts), 4),
            "ragasIdContextPrecision": (
                round(fmean(contextPrecisions), 4) if contextPrecisions else None
            ),
            "ragasIdContextRecall": round(fmean(contextRecalls), 4) if contextRecalls else None,
            "ragasEvaluatedCaseCount": len(contextPrecisions),
        },
        "cutoffMetrics": cutoffMetrics,
        "cases": caseReports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("venues", type=Path)
    parser.add_argument("index", type=Path)
    parser.add_argument("cases", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--cache", type=Path, default=Path("data/local/models"))
    parser.add_argument("--dense-weight", type=float, default=DEFAULT_DENSE_WEIGHT)
    parser.add_argument(
        "--cutoffs",
        type=parseCutoffs,
        default=DEFAULT_EVALUATION_CUTOFFS,
        help="Comma-separated Recall@X/Precision@X cutoffs",
    )
    parser.add_argument("--ollama-model", help="Optional free local Ollama model name")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--ollama-timeout-seconds", type=float, default=8.0)
    parser.add_argument(
        "--ragas-evidence",
        action="store_true",
        help="Add free ID-based Ragas context precision and recall",
    )
    args = parser.parse_args()
    venues = JsonVenueRepository(args.venues).listVenues()
    index = loadIndex(args.index, args.venues)
    cases = TypeAdapter(list[RetrievalEvaluationCase]).validate_json(
        args.cases.read_text(encoding="utf-8")
    )
    provider = FastEmbedProvider(index.modelName, args.cache)
    retriever = HybridRagRetriever(index, provider, denseWeight=args.dense_weight)
    semanticParser = SemanticIntentParser(provider)
    ollamaParser = None
    intentParser: IntentParser = semanticParser
    if args.ollama_model:
        ollamaParser = OllamaIntentParser(
            model=args.ollama_model,
            fallback=semanticParser,
            baseUrl=args.ollama_url,
            timeoutSeconds=args.ollama_timeout_seconds,
        )
        intentParser = ollamaParser
    try:
        evidenceScorer = RagasIdEvidenceScorer() if args.ragas_evidence else None
        report = evaluateCases(
            cases,
            venues,
            retriever,
            intentParser,
            args.cutoffs,
            evidenceScorer,
        )
    finally:
        if ollamaParser is not None:
            ollamaParser.close()
    report["denseWeight"] = args.dense_weight
    report["intentParserVersion"] = intentParser.version
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["aggregate"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
