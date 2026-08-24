"""Tests for bounded official-website RAG passage extraction."""

from datetime import UTC, datetime

import httpx
import pytest

from app.models.provenance import AttributeProvenance
from app.models.venue import Venue
from app.source_enrichment.website_passage_ingester import (
    WebsitePassage,
    WebsitePassageIngester,
    cleanPassages,
    removeCrossVenueBoilerplate,
    splitPassage,
)


def venue(website: str | None = "https://pub.example") -> Venue:
    return Venue(
        venueId="venue-1",
        name="Example Arms",
        latitude=51.5,
        longitude=-0.1,
        area="Westminster",
        website=website,
        tags=["pub"],
        attributeProvenance=(
            {
                "website": AttributeProvenance(
                    source="Official venue website",
                    sourceRecordId=website,
                    verifiedAt="2026-08-09",
                    confidence=1.0,
                )
            }
            if website
            else {}
        ),
    )


def test_ingests_relevant_same_site_passages_with_provenance() -> None:
    pages = {
        "/": """
            <html><body><nav>Food Menu Privacy Policy</nav>
            <main><h1>Example Arms</h1>
            <p>A cosy historic pub serving seasonal British food beside Parliament.</p>
            <div class='customer-reviews'><p>Five stars, highly recommend this pub.</p></div>
            <p>Accept all cookies and manage cookie preferences.</p>
            <a href='/food-menu'>Explore our food menu</a>
            <a href='/contact'>Contact</a></main></body></html>
        """,
        "/food-menu": "<main><p>Our Sunday roast menu uses seasonal ingredients.</p></main>",
    }

    def handleRequest(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(
            200,
            text=pages[request.url.path],
            headers={"content-type": "text/html"},
            request=request,
        )

    report = WebsitePassageIngester(
        delaySeconds=0,
        transport=httpx.MockTransport(handleRequest),
    ).ingest([venue()])

    assert report.venuesScanned == 1
    assert report.pagesFetched == 2
    assert len(report.passages) == 2
    assert all(passage.source == "Official venue website" for passage in report.passages)
    assert {passage.pageUrl for passage in report.passages} == {
        "https://pub.example",
        "https://pub.example/food-menu",
    }
    assert not any("cookie" in passage.text.casefold() for passage in report.passages)
    assert not any("recommend" in passage.text.casefold() for passage in report.passages)


def test_respects_robots_and_reports_missing_or_empty_sites() -> None:
    def handleRequest(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, text="User-agent: *\nDisallow: /", request=request
            )
        raise AssertionError("robots-blocked pages must not be fetched")

    report = WebsitePassageIngester(
        delaySeconds=0,
        transport=httpx.MockTransport(handleRequest),
    ).ingest([venue(), venue(None)])

    assert report.pagesFetched == 0
    assert len(report.failures) == 2
    assert any("non-public" in failure.reason for failure in report.failures)
    assert any("no relevant passages" in failure.reason for failure in report.failures)


def test_rejects_a_website_not_verified_as_official() -> None:
    unverifiedVenue = venue()
    unverifiedVenue.attributeProvenance = {}

    report = WebsitePassageIngester(delaySeconds=0).ingest([unverifiedVenue])

    assert report.venuesScanned == 0
    assert report.failures[0].reason == "website is not verified as official"


def test_cleaning_deduplicates_splits_and_limits_passages() -> None:
    longText = "Historic pub food and drink. " * 40
    passages = cleanPassages(
        ["Short", "Privacy policy for this pub", longText, longText], venue(), limit=2
    )

    assert 1 <= len(passages) <= 2
    assert all(40 <= len(passage) <= 700 for passage in passages)
    assert splitPassage("A" * 900, maxLength=100)[0].endswith("…")


def test_cleaning_excludes_embedded_customer_testimonials() -> None:
    passages = cleanPassages(
        [
            "We stumbled into this pub and highly recommend the food, five stars!",
            "A historic pub serving classic food beside Parliament since 1870.",
        ],
        venue(),
        limit=10,
    )

    assert passages == [
        "A historic pub serving classic food beside Parliament since 1870."
    ]


def test_removes_exact_cross_venue_operator_boilerplate() -> None:
    timestamp = datetime(2026, 8, 9, tzinfo=UTC)
    passages = [
        WebsitePassage(
            venueId=venueId,
            venueName=venueId,
            pageUrl=f"https://example.com/{venueId}",
            text=text,
            retrievedAt=timestamp,
        )
        for venueId, text in (
            ("one", "This repeated pub menu promotion appears on every venue website."),
            ("two", "This repeated pub menu promotion appears on every venue website."),
            ("one", "A unique historic pub beside Parliament with original features."),
        )
    ]

    cleaned = removeCrossVenueBoilerplate(passages)

    assert [passage.text for passage in cleaned] == [
        "A unique historic pub beside Parliament with original features."
    ]


def test_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError, match="limits"):
        WebsitePassageIngester(maxPagesPerVenue=0)


def test_report_timestamp_is_utc() -> None:
    report = WebsitePassageIngester(
        delaySeconds=0,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(404, request=request)
        ),
    ).ingest([])
    assert report.scannedAt.tzinfo == UTC
    assert report.scannedAt <= datetime.now(UTC)
