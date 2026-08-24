"""Grounded passage feature-review tests."""

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app.models.venue import Venue
from app.source_enrichment import passage_feature_review
from app.source_enrichment.passage_feature_review import buildFeatureReview
from app.source_enrichment.website_passage_ingester import (
    WebsitePassage,
    WebsitePassageReport,
)


def testBuildsDeduplicatedManualReviewItems() -> None:
    venue = Venue(
        venueId="venue-1",
        name="Example Arms",
        latitude=51.5,
        longitude=-0.1,
        area="Westminster",
        tags=["pub"],
    )
    passage = WebsitePassage(
        venueId=venue.venueId,
        venueName=venue.name,
        pageUrl="https://example.test/sport",
        text="Example Arms shows live sport every weekend on Sky Sports screens.",
        retrievedAt=datetime(2026, 8, 10, tzinfo=UTC),
    )
    report = WebsitePassageReport(
        scannedAt=datetime(2026, 8, 10, tzinfo=UTC),
        venuesRequested=1,
        venuesScanned=1,
        pagesFetched=1,
        passages=[passage, passage],
        failures=[],
    )

    decisions = buildFeatureReview([venue], report, date(2026, 8, 10))

    assert len(decisions) == 1
    assert decisions[0].feature == "showsSports"
    assert decisions[0].reviewStatus == "manual_review"

    missingVenueReport = report.model_copy(
        update={"passages": [passage.model_copy(update={"venueId": "missing-venue"})]}
    )
    assert buildFeatureReview([venue], missingVenueReport, date(2026, 8, 10)) == []

    alreadyKnown = venue.model_copy(update={"showsSports": True})
    assert buildFeatureReview([alreadyKnown], report, date(2026, 8, 10)) == []


def testCliWritesFeatureReviewQueue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    venuePath = tmp_path / "venues.json"
    passagePath = tmp_path / "passages.json"
    outputPath = tmp_path / "review.json"
    venue = Venue(
        venueId="venue-1",
        name="Example Arms",
        latitude=51.5,
        longitude=-0.1,
        area="Westminster",
        tags=["pub"],
    )
    report = WebsitePassageReport(
        scannedAt=datetime(2026, 8, 10, tzinfo=UTC),
        venuesRequested=1,
        venuesScanned=1,
        pagesFetched=1,
        passages=[
            WebsitePassage(
                venueId=venue.venueId,
                venueName=venue.name,
                pageUrl="https://example.test/music",
                text="Example Arms hosts live music every Friday evening.",
                retrievedAt=datetime(2026, 8, 10, tzinfo=UTC),
            )
        ],
        failures=[],
    )
    venuePath.write_text(TypeAdapter(list[Venue]).dump_json([venue]).decode(), encoding="utf-8")
    passagePath.write_text(report.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "passage-feature-review",
            str(venuePath),
            str(passagePath),
            str(outputPath),
            "--reviewed-at",
            "2026-08-10",
        ],
    )

    assert passage_feature_review.main() == 0
    assert json.loads(outputPath.read_text())[0]["feature"] == "hasLiveMusic"
