"""Build and evaluate a leakage-aware learning-to-rank experiment."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.metrics import accuracy_score, log_loss, roc_auc_score  # type: ignore[import-untyped]
from sklearn.model_selection import GroupShuffleSplit  # type: ignore[import-untyped]
from sklearn.pipeline import make_pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from app.models.feedback import FeedbackEvent, FeedbackEventType
from app.models.venue import Venue
from app.parsing.query_parser import parseQuery
from app.ranking.venue_ranker import calculateDistanceKm, scoreVenue
from app.repositories.json_venue_repository import JsonVenueRepository
from app.repositories.sqlite_feedback_repository import SqliteFeedbackRepository

MIN_LABELS = 100
MIN_POSITIVES = 20
MIN_NEGATIVES = 20
MIN_QUERY_GROUPS = 10

FEATURE_NAMES = (
    "distanceScore",
    "areaMatch",
    "priceRequested",
    "priceKnown",
    "priceMatch",
    "foodRequested",
    "foodKnown",
    "foodMatch",
    "outdoorRequested",
    "outdoorExcluded",
    "outdoorKnown",
    "outdoorMatch",
    "sportsRequested",
    "sportsKnown",
    "sportsMatch",
    "liveMusicRequested",
    "liveMusicKnown",
    "liveMusicMatch",
    "djRequested",
    "djKnown",
    "djMatch",
    "dogFriendlyRequested",
    "dogFriendlyKnown",
    "dogFriendlyMatch",
    "wheelchairRequested",
    "wheelchairKnown",
    "wheelchairMatch",
    "reservationsRequested",
    "reservationsKnown",
    "reservationsMatch",
    "groupRequested",
    "groupKnown",
    "groupMatch",
    "noiseRequested",
    "noiseKnown",
    "noiseMatch",
    "ratingKnown",
    "ratingNormalized",
    "popularityKnown",
    "popularityScore",
)


@dataclass(frozen=True)
class TrainingExample:
    """One explicitly labelled query and venue pair."""

    eventId: str
    sessionId: str
    query: str
    queryGroup: str
    venueId: str
    venueName: str
    label: int
    recommendationRank: int | None
    baselineScore: float
    features: dict[str, float]


def normaliseQuery(query: str) -> str:
    """Normalise superficial query differences for deduplication."""
    return re.sub(r"\s+", " ", query.strip().casefold())


def preferenceSignature(query: str) -> str:
    """Group equivalent parsed preferences to keep them in one data split."""
    preferences = parseQuery(query).model_dump(exclude={"unparsedTerms"})
    return json.dumps(preferences, sort_keys=True, separators=(",", ":"))


def _known(value: object | None) -> float:
    return float(value is not None)


def _requestedFeatures(query: str, venue: Venue) -> dict[str, float]:
    preferences = parseQuery(query)
    distanceKm = calculateDistanceKm(venue, preferences.location)
    distanceScore = (
        max(0.0, 1.0 - distanceKm / 8.0) if distanceKm is not None else 0.0
    )
    outdoorRequested = preferences.requiresOutdoorSeating
    outdoorExcluded = preferences.excludesOutdoorSeating
    outdoorMatch = (
        venue.hasOutdoorSeating is True
        if outdoorRequested
        else venue.hasOutdoorSeating is False
        if outdoorExcluded
        else False
    )
    return {
        "distanceScore": distanceScore,
        "areaMatch": float(bool(preferences.location) and venue.area == preferences.location),
        "priceRequested": float(preferences.priceLevel is not None),
        "priceKnown": _known(venue.priceLevel),
        "priceMatch": float(
            preferences.priceLevel is not None
            and venue.priceLevel == preferences.priceLevel
        ),
        "foodRequested": float(preferences.requiresFood),
        "foodKnown": _known(venue.servesFood),
        "foodMatch": float(preferences.requiresFood and venue.servesFood is True),
        "outdoorRequested": float(outdoorRequested),
        "outdoorExcluded": float(outdoorExcluded),
        "outdoorKnown": _known(venue.hasOutdoorSeating),
        "outdoorMatch": float(outdoorMatch),
        "sportsRequested": float(preferences.showsSports),
        "sportsKnown": _known(venue.showsSports),
        "sportsMatch": float(preferences.showsSports and venue.showsSports is True),
        "liveMusicRequested": float(preferences.requiresLiveMusic),
        "liveMusicKnown": _known(venue.hasLiveMusic),
        "liveMusicMatch": float(
            preferences.requiresLiveMusic and venue.hasLiveMusic is True
        ),
        "djRequested": float(preferences.requiresDj),
        "djKnown": _known(venue.hasDj),
        "djMatch": float(preferences.requiresDj and venue.hasDj is True),
        "dogFriendlyRequested": float(preferences.requiresDogFriendly),
        "dogFriendlyKnown": _known(venue.dogFriendly),
        "dogFriendlyMatch": float(
            preferences.requiresDogFriendly and venue.dogFriendly is True
        ),
        "wheelchairRequested": float(preferences.requiresWheelchairAccess),
        "wheelchairKnown": _known(venue.wheelchairAccessible),
        "wheelchairMatch": float(
            preferences.requiresWheelchairAccess
            and venue.wheelchairAccessible is True
        ),
        "reservationsRequested": float(preferences.requiresReservations),
        "reservationsKnown": _known(venue.acceptsReservations),
        "reservationsMatch": float(
            preferences.requiresReservations and venue.acceptsReservations is True
        ),
        "groupRequested": float(preferences.suitableForGroups),
        "groupKnown": _known(venue.suitableForGroups),
        "groupMatch": float(
            preferences.suitableForGroups and venue.suitableForGroups is True
        ),
        "noiseRequested": float(preferences.noiseLevel is not None),
        "noiseKnown": _known(venue.noiseLevel),
        "noiseMatch": float(
            preferences.noiseLevel is not None
            and venue.noiseLevel == preferences.noiseLevel
        ),
        "ratingKnown": _known(venue.rating),
        "ratingNormalized": venue.rating / 5.0 if venue.rating is not None else 0.0,
        "popularityKnown": _known(venue.popularityScore),
        "popularityScore": venue.popularityScore or 0.0,
    }


def buildTrainingExamples(
    events: list[FeedbackEvent], venues: list[Venue]
) -> tuple[list[TrainingExample], dict[str, int]]:
    """Use the latest explicit label per session, query, and venue."""
    venueById = {venue.venueId: venue for venue in venues}
    explicit = [
        event
        for event in events
        if event.eventType in {FeedbackEventType.like, FeedbackEventType.dislike}
        and event.query
    ]
    latest: dict[tuple[str, str, str], FeedbackEvent] = {}
    for event in sorted(explicit, key=lambda item: item.recordedAt):
        key = (
            event.sessionId or "anonymous",
            normaliseQuery(event.query or ""),
            event.venueId,
        )
        latest[key] = event

    examples: list[TrainingExample] = []
    missingVenues = 0
    for event in latest.values():
        venue = venueById.get(event.venueId)
        if venue is None:
            missingVenues += 1
            continue
        query = event.query or ""
        features = _requestedFeatures(query, venue)
        examples.append(
            TrainingExample(
                eventId=event.eventId,
                sessionId=event.sessionId or "anonymous",
                query=query,
                queryGroup=preferenceSignature(query),
                venueId=venue.venueId,
                venueName=venue.name,
                label=int(event.eventType == FeedbackEventType.like),
                recommendationRank=event.recommendationRank,
                baselineScore=scoreVenue(venue, parseQuery(query)).score,
                features=features,
            )
        )
    return examples, {
        "allEvents": len(events),
        "explicitEvents": len(explicit),
        "deduplicatedExplicitEvents": len(latest),
        "missingVenues": missingVenues,
    }


def readiness(examples: list[TrainingExample]) -> dict[str, Any]:
    """Apply conservative minimums before a model can be considered deployable."""
    labels = Counter(example.label for example in examples)
    queryGroups = len({example.queryGroup for example in examples})
    checks = {
        "atLeast100ExplicitLabels": len(examples) >= MIN_LABELS,
        "atLeast20Likes": labels[1] >= MIN_POSITIVES,
        "atLeast20Dislikes": labels[0] >= MIN_NEGATIVES,
        "atLeast10PreferenceGroups": queryGroups >= MIN_QUERY_GROUPS,
    }
    return {
        "readyForDeployment": all(checks.values()),
        "checks": checks,
        "requirements": {
            "explicitLabels": MIN_LABELS,
            "likes": MIN_POSITIVES,
            "dislikes": MIN_NEGATIVES,
            "preferenceGroups": MIN_QUERY_GROUPS,
        },
        "observed": {
            "explicitLabels": len(examples),
            "likes": labels[1],
            "dislikes": labels[0],
            "preferenceGroups": queryGroups,
        },
    }


def _metrics(labels: list[int], predictions: list[float]) -> dict[str, float]:
    return {
        "rocAuc": round(float(roc_auc_score(labels, predictions)), 4),
        "logLoss": round(float(log_loss(labels, predictions, labels=[0, 1])), 4),
        "accuracyAt0.5": round(
            float(accuracy_score(labels, [value >= 0.5 for value in predictions])), 4
        ),
    }


def evaluateExperiment(examples: list[TrainingExample]) -> dict[str, Any]:
    """Compare current scores with logistic regression on a query-group split."""
    if len(examples) < 4 or len({item.label for item in examples}) < 2:
        return {"available": False, "reason": "Both labels and at least four rows are required."}
    matrix = [[item.features[name] for name in FEATURE_NAMES] for item in examples]
    labels = [item.label for item in examples]
    groups = [item.queryGroup for item in examples]
    if len(set(groups)) < 2:
        return {"available": False, "reason": "At least two preference groups are required."}

    selected: tuple[list[int], list[int]] | None = None
    splitter = GroupShuffleSplit(n_splits=100, test_size=0.3, random_state=42)
    for trainIndices, testIndices in splitter.split(matrix, labels, groups):
        train = trainIndices.tolist()
        test = testIndices.tolist()
        if len({labels[index] for index in train}) == 2 and len(
            {labels[index] for index in test}
        ) == 2:
            selected = train, test
            break
    if selected is None:
        return {
            "available": False,
            "reason": "No query-group split contains likes and dislikes in both partitions.",
        }

    train, test = selected
    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(
            l1_ratio=0, class_weight="balanced", max_iter=1000, random_state=42
        ),
    )
    model.fit([matrix[index] for index in train], [labels[index] for index in train])
    modelPredictions = model.predict_proba([matrix[index] for index in test])[:, 1].tolist()
    baselinePredictions = [
        min(0.999, max(0.001, examples[index].baselineScore)) for index in test
    ]
    classifier = model.named_steps["logisticregression"]
    coefficients = sorted(
        zip(FEATURE_NAMES, classifier.coef_[0].tolist(), strict=True),
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    return {
        "available": True,
        "diagnosticOnly": True,
        "split": {
            "strategy": "GroupShuffleSplit by parsed preference signature",
            "trainRows": len(train),
            "testRows": len(test),
            "trainPreferenceGroups": len({groups[index] for index in train}),
            "testPreferenceGroups": len({groups[index] for index in test}),
        },
        "baselineMetrics": _metrics([labels[index] for index in test], baselinePredictions),
        "modelMetrics": _metrics([labels[index] for index in test], modelPredictions),
        "coefficients": [
            {"feature": name, "coefficient": round(value, 6)}
            for name, value in coefficients
        ],
    }


def writeDataset(examples: list[TrainingExample], outputPath: Path) -> None:
    """Write auditable flat training rows as CSV."""
    outputPath.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "eventId", "sessionId", "query", "queryGroup", "venueId", "venueName",
        "label", "recommendationRank", "baselineScore", *FEATURE_NAMES,
    ]
    with outputPath.open("w", encoding="utf-8", newline="") as outputFile:
        writer = csv.DictWriter(outputFile, fieldnames=fieldnames)
        writer.writeheader()
        for example in examples:
            row = asdict(example)
            features = row.pop("features")
            writer.writerow({**row, **features})


def runExperiment(
    feedbackPath: Path, venuePath: Path, datasetPath: Path, reportPath: Path
) -> dict[str, Any]:
    """Load local data, export examples, and write an evaluation report."""
    if not feedbackPath.exists():
        raise FileNotFoundError(f"Feedback database not found: {feedbackPath}")
    events = SqliteFeedbackRepository(feedbackPath).listEvents()
    venues = JsonVenueRepository(venuePath).listVenues()
    examples, sourceCounts = buildTrainingExamples(events, venues)
    writeDataset(examples, datasetPath)
    readinessReport = readiness(examples)
    evaluation = evaluateExperiment(examples)
    report: dict[str, Any] = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "datasetPath": str(datasetPath),
        "sourceCounts": sourceCounts,
        "featureNames": list(FEATURE_NAMES),
        "readiness": readinessReport,
        "evaluation": evaluation,
        "liveRankingChanged": False,
        "modelArtifactWritten": False,
    }
    reportPath.parent.mkdir(parents=True, exist_ok=True)
    reportPath.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def buildArgumentParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("feedback", type=Path, help="SQLite feedback database")
    parser.add_argument("venues", type=Path, help="Validated venue JSON")
    parser.add_argument("dataset", type=Path, help="Training CSV output")
    parser.add_argument("report", type=Path, help="Experiment JSON output")
    return parser


def main() -> None:
    args = buildArgumentParser().parse_args()
    report = runExperiment(args.feedback, args.venues, args.dataset, args.report)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
