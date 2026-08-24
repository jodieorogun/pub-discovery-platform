"""Official venue website feature-scanner tests."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import TypeAdapter

from app.models.venue import Venue
from app.source_enrichment import official_website_scanner
from app.source_enrichment.official_website_scanner import (
    OfficialWebsiteScanner,
    findFeatureCandidates,
    isPublicHttpUrl,
    readSelectedVenueIds,
)


def venue(website: str | None = "https://pub.example") -> Venue:
    """Build a minimal canonical venue."""
    return Venue(
        venueId="venue-1",
        name="Example Arms",
        latitude=51.5,
        longitude=-0.1,
        area="Westminster",
        website=website,
        tags=["pub"],
    )


def testScansSameSiteFeaturePagesAndPreservesEvidence() -> None:
    pages = {
        "/": """
            <html><body><script>live sports</script><p>Browse our drinks menu.</p>
            <a href='/whats-on'>What's on</a><a href='/accessibility'>Access</a>
            <a href='/book'>Book</a><a href='https://other.example/sport'>Other</a>
            </body></html>
        """,
        "/whats-on": (
            "<p>Watch live sport every weekend, followed by live DJs. "
            "Browse our upcoming events calendar.</p>"
        ),
        "/accessibility": "<p>Dogs are welcome. Wheelchair access and accessible toilets.</p>",
        "/book": "<p>Book a table or contact us about private hire and group bookings.</p>",
    }

    def handleRequest(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=pages[request.url.path],
            request=request,
        )

    report = OfficialWebsiteScanner(
        delaySeconds=0,
        transport=httpx.MockTransport(handleRequest),
    ).scan([venue()])

    assert report.venuesScanned == 1
    assert report.pagesFetched == 4
    assert {candidate.feature for candidate in report.candidates} == {
        "showsSports",
        "hasDj",
        "hostsEvents",
        "dogFriendly",
        "wheelchairAccessible",
        "hasAccessibleToilet",
        "acceptsReservations",
        "suitableForGroups",
    }
    assert all(candidate.reviewStatus == "manual_review" for candidate in report.candidates)
    assert all(candidate.matchedPhrase in candidate.evidenceText for candidate in report.candidates)


def testUsesRobotsRulesAndRejectsUnsafeTargets() -> None:
    def handleRequest(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                text="User-agent: *\nDisallow: /",
                request=request,
            )
        raise AssertionError("blocked page should not be requested")

    report = OfficialWebsiteScanner(
        delaySeconds=0,
        transport=httpx.MockTransport(handleRequest),
    ).scan([venue(), venue("http://127.0.0.1/private")])

    assert report.pagesFetched == 0
    assert len(report.failures) == 1
    assert "non-public" in report.failures[0].reason
    assert isPublicHttpUrl("https://pub.example") is True
    assert isPublicHttpUrl("file:///tmp/page.html") is False
    assert isPublicHttpUrl("http://localhost:8000") is False


def testSkipsBrokenChildPageWithoutDiscardingHomepageEvidence() -> None:
    def handleRequest(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        if request.url.path == "/missing":
            return httpx.Response(404, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<p>Join our happy hour.</p><a href='/missing'>Old page</a>",
            request=request,
        )

    report = OfficialWebsiteScanner(
        delaySeconds=0,
        transport=httpx.MockTransport(handleRequest),
    ).scan([venue()])

    assert report.venuesScanned == 1
    assert report.pagesFetched == 1
    assert {(item.feature, item.suggestedValue) for item in report.candidates} == {
        ("hasHappyHour", True)
    }


def testFeaturePatternsAvoidGenericDrinksMenu() -> None:
    assert findFeatureCandidates(venue(), "https://pub.example", "See our drinks menu") == []
    candidates = findFeatureCandidates(
        venue(),
        "https://pub.example/menu",
        "Our main menu is served daily alongside live music.",
    )
    assert {candidate.feature for candidate in candidates} == {"servesFood", "hasLiveMusic"}


def testFeaturePatternsCaptureNegationAndIgnoreQuestionsAndAssistanceDogs() -> None:
    candidates = findFeatureCandidates(
        venue(),
        "https://pub.example/access",
        (
            "Can I have live music or a DJ? Sadly we cannot have any live music or DJs. "
            "We do not take individual table bookings. Assistance dogs welcome."
        ),
    )
    assert {(candidate.feature, candidate.suggestedValue) for candidate in candidates} == {
        ("hasLiveMusic", False),
        ("hasDj", False),
        ("acceptsReservations", False),
    }
    positiveCandidates = findFeatureCandidates(
        venue(),
        "https://pub.example",
        "If you are not a robot, Book A Table. Not to mention, you can catch live sports.",
    )
    assert {(candidate.feature, candidate.suggestedValue) for candidate in positiveCandidates} == {
        ("acceptsReservations", True),
        ("showsSports", True),
    }


def testFindsNewFeaturesAndPreservesClearNegations() -> None:
    candidates = findFeatureCandidates(
        venue(),
        "https://pub.example/offers",
        (
            "Join our happy hour. We serve a Sunday roast and vegan options. "
            "Our weekly pub quiz is Tuesday. We have an accessible toilet. "
            "There is no happy hour on bank holidays and we do not host a quiz night in August."
        ),
    )

    assert {(item.feature, item.suggestedValue) for item in candidates} >= {
        ("hasHappyHour", True),
        ("hasHappyHour", False),
        ("servesSundayRoast", True),
        ("hasVeganOptions", True),
        ("hostsQuiz", True),
        ("hostsQuiz", False),
        ("hasAccessibleToilet", True),
    }


def testReadsAuditVenueIds(tmp_path: Path) -> None:
    auditPath = tmp_path / "audit.json"
    auditPath.write_text(
        json.dumps([{"venueId": "venue-1"}, {"venueId": "venue-2"}]),
        encoding="utf-8",
    )
    assert readSelectedVenueIds(auditPath) == {"venue-1", "venue-2"}
    auditPath.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON list"):
        readSelectedVenueIds(auditPath)


def testCliWritesReviewReport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    venuePath = tmp_path / "venues.json"
    selectedPath = tmp_path / "selected.json"
    outputPath = tmp_path / "report.json"
    venuePath.write_text(
        TypeAdapter(list[Venue]).dump_json([venue()]).decode(),
        encoding="utf-8",
    )
    selectedPath.write_text('["venue-1"]', encoding="utf-8")
    expectedReport = official_website_scanner.WebsiteFeatureReport(
        scannedAt=datetime(2026, 8, 8, 12, tzinfo=UTC),
        venuesRequested=1,
        venuesScanned=1,
        pagesFetched=1,
        candidates=[],
        failures=[],
    )
    monkeypatch.setattr(
        official_website_scanner.OfficialWebsiteScanner,
        "scan",
        lambda self, venues: expectedReport,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "official-website-scanner",
            str(venuePath),
            str(outputPath),
            "--venue-ids-file",
            str(selectedPath),
            "--delay-seconds",
            "0",
        ],
    )

    assert official_website_scanner.main() == 0
    assert json.loads(outputPath.read_text(encoding="utf-8"))["venuesScanned"] == 1
