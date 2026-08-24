"""Official website URL verification tests."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pydantic import TypeAdapter

from app.models.venue import Venue
from app.source_enrichment import official_url_verifier
from app.source_enrichment.official_url_verifier import (
    OfficialUrlAuditReport,
    UrlVerificationResult,
    verifyVenueUrl,
)


def venue(website: str = "https://example-arms.example/venue/example-arms") -> Venue:
    return Venue(
        venueId="venue-1",
        name="Example Arms",
        latitude=51.5,
        longitude=-0.1,
        area="Westminster",
        website=website,
        tags=["pub"],
    )


def testVerifiesPageWithIndependentVenueIdentitySignals() -> None:
    def handleRequest(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<title>Example Arms | Official Pub</title><h1>Example Arms</h1>"
                "<script>untrusted hidden content</script>"
            ),
            request=request,
        )

    result = verifyVenueUrl(
        venue(), transport=httpx.MockTransport(handleRequest)
    )

    assert result.status == "verified"
    assert "URL identifies venue" in result.signals
    assert "full venue name appears on page" in result.signals


def testRejectsGenericOperatorHomepageAndThirdPartyDomain() -> None:
    def handleRequest(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(
            200,
            text="<title>Example Pub Company</title><p>Explore all our pubs.</p>",
            request=request,
        )

    generic = verifyVenueUrl(
        venue("https://operator.example"),
        transport=httpx.MockTransport(handleRequest),
    )
    thirdParty = verifyVenueUrl(venue("https://tripadvisor.com/example-arms"))

    assert generic.status == "rejected"
    assert thirdParty.status == "rejected"
    assert "third-party" in thirdParty.reason or "directory" in thirdParty.reason


def testCliWritesAuditAndPromotableVerificationFiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venuePath = tmp_path / "venues.json"
    reportPath = tmp_path / "audit.json"
    verificationPath = tmp_path / "verified.json"
    venuePath.write_text(
        TypeAdapter(list[Venue]).dump_json([venue()]).decode(), encoding="utf-8"
    )
    expected = OfficialUrlAuditReport(
        auditedAt=datetime(2026, 8, 10, tzinfo=UTC),
        venuesRequested=1,
        verifiedCount=1,
        rejectedCount=0,
        results=[
            UrlVerificationResult(
                venueId="venue-1",
                venueName="Example Arms",
                candidateUrl="https://example-arms.example/venue/example-arms",
                finalUrl="https://example-arms.example/venue/example-arms",
                status="verified",
                signals=["URL identifies venue", "full venue name appears on page"],
                reason="Multiple first-party identity signals verified the venue page",
            )
        ],
    )
    monkeypatch.setattr(official_url_verifier, "auditUrls", lambda *args, **kwargs: expected)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "official-url-verifier",
            str(venuePath),
            str(reportPath),
            str(verificationPath),
            "--verified-at",
            "2026-08-10",
        ],
    )

    assert official_url_verifier.main() == 0
    assert json.loads(reportPath.read_text())["verifiedCount"] == 1
    verification = json.loads(verificationPath.read_text())[0]
    assert verification["venueId"] == "venue-1"
    assert verification["verifiedAt"] == "2026-08-10"
