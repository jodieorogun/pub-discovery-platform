"""Human feature-review queue API tests."""

import sqlite3
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app.repositories.sqlite_feature_review_repository import (
    FeatureReviewDecision,
    SqliteFeatureReviewRepository,
)


def seedReview(databasePath: Path) -> SqliteFeatureReviewRepository:
    repository = SqliteFeatureReviewRepository(databasePath)
    with sqlite3.connect(databasePath) as connection:
        connection.execute(
            """
            INSERT INTO feature_reviews(
                venue_id, venue_name, feature, suggested_value, evidence_url,
                evidence_text, review_status, review_reason, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "venue-1",
                "Example Arms",
                "hasLiveMusic",
                1,
                "https://example.test/music",
                "Live music every Friday.",
                "manual_review",
                "Awaiting review",
                date(2026, 8, 12).isoformat(),
            ),
        )
    return repository


def testRepositoryListsAndDecidesReviewItems(tmp_path: Path) -> None:
    repository = seedReview(tmp_path / "app.sqlite3")
    pending = repository.listItems()

    assert len(pending) == 1
    decided = repository.decide(
        pending[0].reviewId,
        FeatureReviewDecision(
            reviewStatus="approved", reviewReason="Official page explicitly confirms it."
        ),
    )

    assert decided.reviewStatus == "approved"
    assert repository.listItems() == []


def testReviewApiListsAndUpdatesItems(client: TestClient, tmp_path: Path) -> None:
    databasePath = tmp_path / "feedback.sqlite3"
    repository = seedReview(databasePath)
    item = repository.listItems()[0]

    response = client.get("/review/features")
    assert response.status_code == 200
    assert response.json()[0]["feature"] == "hasLiveMusic"

    updated = client.patch(
        f"/review/features/{item.reviewId}",
        json={"reviewStatus": "rejected", "reviewReason": "The passage is ambiguous."},
    )
    assert updated.status_code == 200
    assert updated.json()["reviewStatus"] == "rejected"
    assert client.patch(
        "/review/features/999999",
        json={"reviewStatus": "rejected", "reviewReason": "No such review item."},
    ).status_code == 404
