"""Tests for the guarded offline ML experiment."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.ml.offline_experiment import (
    FEATURE_NAMES,
    buildTrainingExamples,
    evaluateExperiment,
    normaliseQuery,
    readiness,
    runExperiment,
)
from app.models.feedback import FeedbackEvent, FeedbackEventType
from app.models.venue import Venue
from app.repositories.sqlite_feedback_repository import SqliteFeedbackRepository


def venue(venueId: str = "venue-1") -> Venue:
    return Venue(
        venueId=venueId,
        name=f"Pub {venueId}",
        latitude=51.501,
        longitude=-0.13,
        area="Westminster",
        priceLevel="moderate",
        servesFood=True,
        hasOutdoorSeating=False,
        showsSports=True,
        hasLiveMusic=True,
        hasDj=False,
        dogFriendly=True,
        wheelchairAccessible=True,
        acceptsReservations=True,
        suitableForGroups=True,
        noiseLevel="lively",
        rating=4.5,
        popularityScore=0.8,
        tags=[],
    )


def event(
    eventId: str,
    eventType: FeedbackEventType,
    query: str | None = "pub in Westminster with food",
    venueId: str = "venue-1",
    minutes: int = 0,
    sessionId: str | None = "session-1",
) -> FeedbackEvent:
    return FeedbackEvent(
        eventId=eventId,
        venueId=venueId,
        eventType=eventType,
        query=query,
        recommendationRank=1,
        sessionId=sessionId,
        recordedAt=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minutes),
    )


def test_build_examples_uses_latest_explicit_label_and_skips_missing_data() -> None:
    events = [
        event("impression", FeedbackEventType.impression),
        event("old", FeedbackEventType.like),
        event("latest", FeedbackEventType.dislike, minutes=1),
        event("no-query", FeedbackEventType.like, query=None),
        event("missing", FeedbackEventType.like, venueId="missing"),
    ]
    examples, counts = buildTrainingExamples(events, [venue()])

    assert len(examples) == 1
    assert examples[0].eventId == "latest"
    assert examples[0].label == 0
    assert tuple(examples[0].features) == FEATURE_NAMES
    assert examples[0].features["foodMatch"] == 1.0
    assert counts == {
        "allEvents": 5,
        "explicitEvents": 3,
        "deduplicatedExplicitEvents": 2,
        "missingVenues": 1,
    }
    assert normaliseQuery(" PUB   in Westminster ") == "pub in westminster"


def test_readiness_reports_each_missing_threshold() -> None:
    examples, _ = buildTrainingExamples(
        [event("like", FeedbackEventType.like), event("dislike", FeedbackEventType.dislike,
         query="pub in Westminster without outdoor seating", venueId="venue-2")],
        [venue(), venue("venue-2")],
    )
    report = readiness(examples)

    assert report["readyForDeployment"] is False
    assert report["observed"]["explicitLabels"] == 2
    assert not all(report["checks"].values())


def test_evaluation_explains_when_a_safe_split_is_impossible() -> None:
    one, _ = buildTrainingExamples([event("like", FeedbackEventType.like)], [venue()])
    assert evaluateExperiment(one)["available"] is False

    events = [
        event(f"like-{index}", FeedbackEventType.like, sessionId=f"s-{index}")
        for index in range(3)
    ] + [
        event(
            f"dislike-{index}",
            FeedbackEventType.dislike,
            query="pub in Westminster without outdoor seating",
            sessionId=f"d-{index}",
        )
        for index in range(3)
    ]
    examples, _ = buildTrainingExamples(events, [venue()])
    assert "No query-group split" in evaluateExperiment(examples)["reason"]


def test_evaluation_trains_on_synthetic_query_groups() -> None:
    events: list[FeedbackEvent] = []
    queries = [
        "pub in Westminster with food",
        "pub in Westminster with sports",
        "pub in Westminster with live music",
        "pub in Westminster dogs welcome",
    ]
    venues = [venue("venue-1"), venue("venue-2")]
    venues[1].servesFood = False
    venues[1].showsSports = False
    venues[1].hasLiveMusic = False
    venues[1].dogFriendly = False
    for queryIndex, query in enumerate(queries):
        events.extend(
            [
                event(
                    f"like-{queryIndex}", FeedbackEventType.like, query=query,
                    venueId="venue-1", sessionId=f"like-{queryIndex}",
                ),
                event(
                    f"dislike-{queryIndex}", FeedbackEventType.dislike, query=query,
                    venueId="venue-2", sessionId=f"dislike-{queryIndex}",
                ),
            ]
        )
    examples, _ = buildTrainingExamples(events, venues)
    report = evaluateExperiment(examples)

    assert report["available"] is True
    assert report["diagnosticOnly"] is True
    assert set(report["modelMetrics"]) == {"rocAuc", "logLoss", "accuracyAt0.5"}
    assert len(report["coefficients"]) == len(FEATURE_NAMES)


def test_run_experiment_writes_auditable_outputs(tmp_path: Path) -> None:
    databasePath = tmp_path / "feedback.sqlite3"
    repository = SqliteFeedbackRepository(databasePath)
    repository.record(event("like", FeedbackEventType.like))
    venuePath = tmp_path / "venues.json"
    venuePath.write_text("[" + venue().model_dump_json() + "]", encoding="utf-8")
    datasetPath = tmp_path / "dataset.csv"
    reportPath = tmp_path / "report.json"

    report = runExperiment(databasePath, venuePath, datasetPath, reportPath)

    assert datasetPath.read_text(encoding="utf-8").startswith("eventId,sessionId")
    assert reportPath.exists()
    assert report["liveRankingChanged"] is False
    assert report["modelArtifactWritten"] is False


def test_run_experiment_rejects_missing_feedback_database(tmp_path: Path) -> None:
    try:
        runExperiment(
            tmp_path / "missing.sqlite3",
            tmp_path / "venues.json",
            tmp_path / "dataset.csv",
            tmp_path / "report.json",
        )
    except FileNotFoundError as error:
        assert "Feedback database not found" in str(error)
    else:
        raise AssertionError("Expected missing feedback to fail")
