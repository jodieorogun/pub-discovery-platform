"""SQLite-backed recommendation feedback storage."""

import sqlite3
from datetime import datetime
from pathlib import Path

from app.models.feedback import FeedbackEvent, FeedbackEventType


class SqliteFeedbackRepository:
    """Store anonymous feedback locally with no external service cost."""

    def __init__(self, databasePath: Path) -> None:
        self.databasePath = databasePath
        self.databasePath.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.databasePath) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_events (
                    event_id TEXT PRIMARY KEY,
                    venue_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    search_query TEXT,
                    recommendation_rank INTEGER,
                    session_id TEXT,
                    request_id TEXT,
                    ranking_version TEXT,
                    dataset_version TEXT,
                    recorded_at TEXT NOT NULL
                )
                """
            )
            # Existing local databases are upgraded in place without a migration dependency.
            existingColumns = {
                row[1] for row in connection.execute("PRAGMA table_info(feedback_events)")
            }
            for columnName in ("request_id", "ranking_version", "dataset_version"):
                if columnName not in existingColumns:
                    connection.execute(
                        f"ALTER TABLE feedback_events ADD COLUMN {columnName} TEXT"
                    )

    def record(self, event: FeedbackEvent) -> None:
        """Insert one immutable feedback event."""
        with sqlite3.connect(self.databasePath) as connection:
            connection.execute(
                """
                INSERT INTO feedback_events (
                    event_id, venue_id, event_type, search_query,
                    recommendation_rank, session_id, request_id,
                    ranking_version, dataset_version, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.eventId,
                    event.venueId,
                    event.eventType.value,
                    event.query,
                    event.recommendationRank,
                    event.sessionId,
                    event.requestId,
                    event.rankingVersion,
                    event.datasetVersion,
                    event.recordedAt.isoformat(),
                ),
            )

    def listEvents(self) -> list[FeedbackEvent]:
        """Load events in insertion order for offline evaluation."""
        with sqlite3.connect(self.databasePath) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT event_id, venue_id, event_type, search_query,
                       recommendation_rank, session_id, request_id,
                       ranking_version, dataset_version, recorded_at
                FROM feedback_events
                ORDER BY rowid
                """
            ).fetchall()
        return [
            FeedbackEvent(
                eventId=row["event_id"],
                venueId=row["venue_id"],
                eventType=FeedbackEventType(row["event_type"]),
                query=row["search_query"],
                recommendationRank=row["recommendation_rank"],
                sessionId=row["session_id"],
                requestId=row["request_id"],
                rankingVersion=row["ranking_version"],
                datasetVersion=row["dataset_version"],
                recordedAt=datetime.fromisoformat(row["recorded_at"]),
            )
            for row in rows
        ]
