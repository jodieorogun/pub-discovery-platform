"""Verify candidate venue URLs before treating them as official RAG sources."""

import argparse
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from app.models.venue import Venue
from app.source_enrichment.official_website_scanner import isPublicHttpUrl
from app.source_matching.source_matcher import normaliseName

VERIFIER_USER_AGENT = "ai-recommendation-engine/0.1 official-url-audit"
BLOCKED_DOMAINS = {
    "beerintheevening.com",
    "facebook.com",
    "instagram.com",
    "pubs.com",
    "royalexchange.co.uk",
    "tripadvisor.co.uk",
    "tripadvisor.com",
    "cubitthouse.wpengine.com",
}
GENERIC_NAME_WORDS = {
    "and",
    "bar",
    "hotel",
    "london",
    "of",
    "pub",
    "public",
    "restaurant",
    "the",
}


class OfficialWebsiteVerification(BaseModel):
    """One reviewed URL that may be promoted to official provenance."""

    venueId: str = Field(min_length=1)
    website: str = Field(pattern=r"^https?://", min_length=1)
    evidenceUrl: str = Field(pattern=r"^https?://", min_length=1)
    verifiedAt: date
    reason: str = Field(min_length=1)


class UrlVerificationResult(BaseModel):
    """Auditable result for one candidate URL."""

    venueId: str
    venueName: str
    candidateUrl: str | None
    finalUrl: str | None = None
    status: str
    signals: list[str] = Field(default_factory=list)
    reason: str


class OfficialUrlAuditReport(BaseModel):
    """Complete output from a bounded URL identity audit."""

    auditedAt: datetime
    venuesRequested: int
    verifiedCount: int
    rejectedCount: int
    results: list[UrlVerificationResult]


class IdentityHtmlParser(HTMLParser):
    """Extract page title, first heading, and visible text without executing scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hiddenDepth = 0
        self.captureTitle = False
        self.captureHeading = False
        self.titleParts: list[str] = []
        self.headingParts: list[str] = []
        self.textParts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hiddenDepth += 1
        if tag == "title":
            self.captureTitle = True
        if tag == "h1" and not self.headingParts:
            self.captureHeading = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.hiddenDepth:
            self.hiddenDepth -= 1
        if tag == "title":
            self.captureTitle = False
        if tag == "h1":
            self.captureHeading = False

    def handle_data(self, data: str) -> None:
        if self.hiddenDepth or not data.strip():
            return
        self.textParts.append(data)
        if self.captureTitle:
            self.titleParts.append(data)
        if self.captureHeading:
            self.headingParts.append(data)

    @staticmethod
    def _clean(parts: list[str]) -> str:
        return re.sub(r"\s+", " ", " ".join(parts)).strip()

    @property
    def title(self) -> str:
        return self._clean(self.titleParts)

    @property
    def heading(self) -> str:
        return self._clean(self.headingParts)

    @property
    def visibleText(self) -> str:
        return self._clean(self.textParts)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold().removeprefix("www.")


def _identityTokens(name: str) -> list[str]:
    return [
        token
        for token in normaliseName(name).split()
        if token not in GENERIC_NAME_WORDS and len(token) > 2
    ]


def identitySignals(venue: Venue, finalUrl: str, parser: IdentityHtmlParser) -> list[str]:
    """Return independent signals tying a fetched page to a venue identity."""
    normalisedVenue = normaliseName(venue.name)
    pageIdentity = normaliseName(f"{parser.title} {parser.heading}")
    visibleText = normaliseName(parser.visibleText)
    urlText = normaliseName(finalUrl)
    tokens = _identityTokens(venue.name)
    signals: list[str] = []
    if normalisedVenue and normalisedVenue in visibleText:
        signals.append("full venue name appears on page")
    if normalisedVenue and SequenceMatcher(None, normalisedVenue, pageIdentity).ratio() >= 0.58:
        signals.append("page title or heading matches venue name")
    if tokens and all(token in urlText for token in tokens):
        signals.append("URL identifies venue")
    if tokens and all(token in pageIdentity for token in tokens):
        signals.append("title or heading contains distinctive venue words")
    hasTrackedOfficialEvidence = any(
        provenance.source == "Official venue website"
        for provenance in venue.attributeProvenance.values()
    )
    if (
        hasTrackedOfficialEvidence
        and venue.sourceUrl
        and _host(venue.sourceUrl) == _host(finalUrl)
    ):
        signals.append("tracked official evidence uses same domain")
    return signals


def _robots(client: httpx.Client, url: str) -> RobotFileParser | None:
    parts = urlsplit(url)
    robotsUrl = f"{parts.scheme}://{parts.netloc}/robots.txt"
    response = client.get(robotsUrl)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    parser = RobotFileParser()
    parser.set_url(robotsUrl)
    parser.parse(response.text.splitlines())
    return parser


def verifyVenueUrl(
    venue: Venue,
    *,
    timeoutSeconds: float = 12,
    delaySeconds: float = 0,
    transport: httpx.BaseTransport | None = None,
) -> UrlVerificationResult:
    """Fetch one candidate and accept only strong first-party identity evidence."""
    candidateUrl = venue.website
    if not candidateUrl or not isPublicHttpUrl(candidateUrl):
        return UrlVerificationResult(
            venueId=venue.venueId,
            venueName=venue.name,
            candidateUrl=candidateUrl,
            status="rejected",
            reason="missing or non-public website",
        )
    if _host(candidateUrl) in BLOCKED_DOMAINS:
        return UrlVerificationResult(
            venueId=venue.venueId,
            venueName=venue.name,
            candidateUrl=candidateUrl,
            status="rejected",
            reason="known directory, review, or social domain",
        )
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=timeoutSeconds,
            transport=transport,
            headers={"User-Agent": VERIFIER_USER_AGENT, "Accept": "text/html"},
        ) as client:
            robotParser = _robots(client, candidateUrl)
            if robotParser and not robotParser.can_fetch(VERIFIER_USER_AGENT, candidateUrl):
                raise ValueError("robots.txt blocks verification")
            if delaySeconds:
                time.sleep(delaySeconds)
            response = client.get(candidateUrl)
            response.raise_for_status()
    except (httpx.HTTPError, ValueError) as error:
        return UrlVerificationResult(
            venueId=venue.venueId,
            venueName=venue.name,
            candidateUrl=candidateUrl,
            status="rejected",
            reason=str(error),
        )
    if "text/html" not in response.headers.get("content-type", "text/html"):
        return UrlVerificationResult(
            venueId=venue.venueId,
            venueName=venue.name,
            candidateUrl=candidateUrl,
            finalUrl=str(response.url),
            status="rejected",
            reason="website did not return HTML",
        )
    if _host(str(response.url)) in BLOCKED_DOMAINS:
        return UrlVerificationResult(
            venueId=venue.venueId,
            venueName=venue.name,
            candidateUrl=candidateUrl,
            finalUrl=str(response.url),
            status="rejected",
            reason="redirected to a known third-party domain",
        )
    parser = IdentityHtmlParser()
    parser.feed(response.text)
    signals = identitySignals(venue, str(response.url), parser)
    # Two independent signals avoid promoting generic operator homepages by name alone.
    verified = len(signals) >= 2 and (
        "URL identifies venue" in signals
        or "page title or heading matches venue name" in signals
        or "tracked official evidence uses same domain" in signals
    )
    return UrlVerificationResult(
        venueId=venue.venueId,
        venueName=venue.name,
        candidateUrl=candidateUrl,
        finalUrl=str(response.url),
        status="verified" if verified else "rejected",
        signals=signals,
        reason=(
            "Multiple first-party identity signals verified the venue page"
            if verified
            else "insufficient evidence that the page is specific to this venue"
        ),
    )


def auditUrls(
    venues: list[Venue], *, timeoutSeconds: float, workers: int
) -> OfficialUrlAuditReport:
    """Verify candidate URLs concurrently while retaining deterministic output order."""
    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(
            executor.map(
                lambda venue: verifyVenueUrl(venue, timeoutSeconds=timeoutSeconds), venues
            )
        )
    verifiedCount = sum(result.status == "verified" for result in results)
    return OfficialUrlAuditReport(
        auditedAt=datetime.now(UTC),
        venuesRequested=len(venues),
        verifiedCount=verifiedCount,
        rejectedCount=len(results) - verifiedCount,
        results=results,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("venues", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("verifications", type=Path)
    parser.add_argument("--verified-at", required=True, type=date.fromisoformat)
    parser.add_argument("--timeout-seconds", type=float, default=12)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    venues = TypeAdapter(list[Venue]).validate_json(args.venues.read_text(encoding="utf-8"))
    candidates = [
        venue
        for venue in venues
        if venue.website
        and venue.attributeProvenance.get("website", None) is not None
        and venue.attributeProvenance["website"].source != "Official venue website"
    ]
    report = auditUrls(
        candidates, timeoutSeconds=args.timeout_seconds, workers=args.workers
    )
    verifications = [
        OfficialWebsiteVerification(
            venueId=result.venueId,
            website=result.finalUrl or result.candidateUrl or "",
            evidenceUrl=result.finalUrl or result.candidateUrl or "",
            verifiedAt=args.verified_at,
            reason=result.reason + ": " + ", ".join(result.signals),
        )
        for result in report.results
        if result.status == "verified"
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    args.verifications.parent.mkdir(parents=True, exist_ok=True)
    args.verifications.write_text(
        TypeAdapter(list[OfficialWebsiteVerification])
        .dump_json(verifications, indent=2)
        .decode()
        + "\n",
        encoding="utf-8",
    )
    print(
        f"Verified {report.verifiedCount} of {report.venuesRequested} candidate website URLs"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
