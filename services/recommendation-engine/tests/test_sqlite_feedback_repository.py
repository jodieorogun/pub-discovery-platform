"""SQLite feedback persistence tests."""

from datetime import UTC, datetime
from pathlib import Path

from app.models.feedback import FeedbackEvent, FeedbackEventType
from app.repositories.sqlite_feedback_repository import SqliteFeedbackRepository


def testPersistsAndReloadsFeedback(tmp_path: Path) -> None:
    databasePath = tmp_path / "nested" / "feedback.sqlite3"
    repository = SqliteFeedbackRepository(databasePath)
    event = FeedbackEvent(
        eventId="event-1",
        venueId="venue-001",
        eventType=FeedbackEventType.like,
        query="pub in Westminster",
        recommendationRank=2,
        sessionId="session-1",
        recordedAt=datetime(2026, 8, 8, 12, tzinfo=UTC),
    )

    repository.record(event)

    assert SqliteFeedbackRepository(databasePath).listEvents() == [event]
