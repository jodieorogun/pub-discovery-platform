"""Create a deduplicated feature-review queue from grounded website passages."""

import argparse
import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter

from app.models.venue import Venue
from app.preference_features import FEATURE_ATTRIBUTES
from app.source_enrichment.official_website_scanner import findFeatureCandidates
from app.source_enrichment.website_passage_ingester import WebsitePassageReport

SUPPORTED_FEATURES = {
    "acceptsReservations",
    "dogFriendly",
    "hasDj",
    "hasLiveMusic",
    "hostsEvents",
    "servesFood",
    "showsSports",
    "suitableForGroups",
    "wheelchairAccessible",
    "hasHappyHour",
    "servesSundayRoast",
    "hasVeganOptions",
    "hostsQuiz",
    "hasAccessibleToilet",
}


class PassageFeatureDecision(BaseModel):
    """One reviewed structured fact with attributable official-site evidence."""

    venueId: str
    venueName: str
    feature: str
    suggestedValue: bool
    evidenceUrl: str = Field(pattern=r"^https?://")
    evidenceText: str = Field(min_length=1)
    reviewedAt: date
    reviewStatus: Literal["manual_review", "approved", "rejected"] = "manual_review"
    reviewReason: str = "Awaiting review"


def buildFeatureReview(
    venues: list[Venue], passageReport: WebsitePassageReport, reviewedAt: date
) -> list[PassageFeatureDecision]:
    """Extract one review item per venue, feature, and suggested value."""
    venueById = {venue.venueId: venue for venue in venues}
    decisions: dict[tuple[str, str, bool], PassageFeatureDecision] = {}
    for passage in passageReport.passages:
        venue = venueById.get(passage.venueId)
        if venue is None:
            continue
        for candidate in findFeatureCandidates(venue, passage.pageUrl, passage.text):
            if candidate.feature not in SUPPORTED_FEATURES:
                continue
            # Do not ask a reviewer to reconfirm a fact already present in the
            # canonical record with the same value.
            featureName = next(
                (
                    feature
                    for feature, attribute in FEATURE_ATTRIBUTES.items()
                    if attribute == candidate.feature
                ),
                None,
            )
            currentValue = getattr(venue, candidate.feature)
            if featureName is not None and currentValue is candidate.suggestedValue:
                continue
            key = (candidate.venueId, candidate.feature, candidate.suggestedValue)
            decisions.setdefault(
                key,
                PassageFeatureDecision(
                    venueId=candidate.venueId,
                    venueName=candidate.venueName,
                    feature=candidate.feature,
                    suggestedValue=candidate.suggestedValue,
                    evidenceUrl=candidate.pageUrl,
                    evidenceText=candidate.evidenceText,
                    reviewedAt=reviewedAt,
                ),
            )
    return sorted(
        decisions.values(),
        key=lambda item: (item.venueName.casefold(), item.feature, item.suggestedValue),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("venues", type=Path)
    parser.add_argument("passages", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--reviewed-at", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--database",
        type=Path,
        help="Also insert manual-review items into the local application database",
    )
    args = parser.parse_args()
    venues = TypeAdapter(list[Venue]).validate_json(args.venues.read_text(encoding="utf-8"))
    passageReport = WebsitePassageReport.model_validate_json(
        args.passages.read_text(encoding="utf-8")
    )
    decisions = buildFeatureReview(venues, passageReport, args.reviewed_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        TypeAdapter(list[PassageFeatureDecision]).dump_json(decisions, indent=2).decode() + "\n",
        encoding="utf-8",
    )
    if args.database:
        _writeReviewQueue(args.database, decisions)
    print(f"Created {len(decisions)} passage feature review items")
    return 0


def _writeReviewQueue(databasePath: Path, decisions: list[PassageFeatureDecision]) -> None:
    """Insert new claims idempotently; existing human decisions stay untouched."""
    from app.repositories.sqlite_feature_review_repository import (
        SqliteFeatureReviewRepository,
    )

    SqliteFeatureReviewRepository(databasePath)
    with sqlite3.connect(databasePath) as connection:
        # Refresh only undecided work. Historical approve/reject decisions are
        # retained, while stale pending duplicates disappear after a rebuild.
        connection.execute("DELETE FROM feature_reviews WHERE review_status = 'manual_review'")
        connection.executemany(
            """
            INSERT OR IGNORE INTO feature_reviews(
                venue_id, venue_name, feature, suggested_value, evidence_url,
                evidence_text, review_status, review_reason, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    item.venueId,
                    item.venueName,
                    item.feature,
                    int(item.suggestedValue),
                    item.evidenceUrl,
                    item.evidenceText,
                    item.reviewStatus,
                    item.reviewReason,
                    item.reviewedAt.isoformat(),
                )
                for item in decisions
            ),
        )


if __name__ == "__main__":
    raise SystemExit(main())
