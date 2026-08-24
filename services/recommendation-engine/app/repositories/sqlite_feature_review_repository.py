"""Local SQLite queue for reviewing extracted venue facts."""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field


class FeatureReviewItem(BaseModel):
    """One official-site claim awaiting a human decision."""

    reviewId: int
    venueId: str
    venueName: str
    feature: str
    suggestedValue: bool
    evidenceUrl: str
    evidenceText: str
    reviewStatus: Literal["manual_review", "approved", "rejected"]
    reviewReason: str
    reviewedAt: date


class FeatureReviewDecision(BaseModel):
    """A human approve/reject action from the local interface."""

    reviewStatus: Literal["approved", "rejected"]
    reviewReason: str = Field(min_length=3, max_length=500)


class SqliteFeatureReviewRepository:
    """Persist review decisions next to venue evidence without paid services."""

    def __init__(self, databasePath: Path) -> None:
        self.databasePath = databasePath
        with sqlite3.connect(databasePath) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS feature_reviews (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venue_id TEXT NOT NULL,
                    venue_name TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    suggested_value INTEGER NOT NULL CHECK(suggested_value IN (0, 1)),
                    evidence_url TEXT NOT NULL,
                    evidence_text TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    review_reason TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    UNIQUE(venue_id, feature, suggested_value, evidence_url, evidence_text)
                )
                """
            )

    def listItems(self, status: str = "manual_review", limit: int = 50) -> list[FeatureReviewItem]:
        """Return a small, stable page of reviewable facts."""
        with sqlite3.connect(self.databasePath) as connection:
            rows = connection.execute(
                """
                SELECT review_id, venue_id, venue_name, feature, suggested_value,
                       evidence_url, evidence_text, review_status, review_reason, reviewed_at
                FROM feature_reviews
                WHERE review_status = ?
                ORDER BY venue_name COLLATE NOCASE, feature, review_id
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        return [
            FeatureReviewItem(
                reviewId=int(row[0]),
                venueId=str(row[1]),
                venueName=str(row[2]),
                feature=str(row[3]),
                suggestedValue=bool(row[4]),
                evidenceUrl=str(row[5]),
                evidenceText=str(row[6]),
                reviewStatus=cast(
                    Literal["manual_review", "approved", "rejected"], str(row[7])
                ),
                reviewReason=str(row[8]),
                reviewedAt=date.fromisoformat(str(row[9])),
            )
            for row in rows
        ]

    def decide(self, reviewId: int, decision: FeatureReviewDecision) -> FeatureReviewItem:
        """Apply one explicit human decision and return the updated item."""
        with sqlite3.connect(self.databasePath) as connection:
            updated = connection.execute(
                """
                UPDATE feature_reviews
                SET review_status = ?, review_reason = ?, reviewed_at = date('now')
                WHERE review_id = ?
                """,
                (decision.reviewStatus, decision.reviewReason, reviewId),
            ).rowcount
        if updated == 0:
            raise KeyError(reviewId)
        for status in ("approved", "rejected"):
            item = next((x for x in self.listItems(status, 10000) if x.reviewId == reviewId), None)
            if item is not None:
                return item
        raise KeyError(reviewId)
