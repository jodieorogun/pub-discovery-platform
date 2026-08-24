"""Find reviewable pub-feature evidence on official venue websites."""

import argparse
import ipaddress
import json
import re
import time
from collections import deque
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, Field, TypeAdapter

from app.models.venue import Venue

SCANNER_USER_AGENT = "ai-recommendation-engine/0.1 website-feature-audit"
FEATURE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "servesFood": (
        re.compile(r"\b(?:food|main|lunch|dinner|sunday|seasonal) menus?\b", re.I),
        re.compile(r"\b(?:food|dining) (?:and|&) drinks?\b", re.I),
    ),
    "showsSports": (
        re.compile(r"\blive sports?\b", re.I),
        re.compile(r"\b(?:sky|tnt) sports?\b", re.I),
        re.compile(r"\bwatch (?:the )?(?:football|rugby|sport)\b", re.I),
    ),
    "hasLiveMusic": (re.compile(r"\blive music\b", re.I),),
    "hasDj": (re.compile(r"\b(?:live )?dj(?:s| nights?| sets?)?\b", re.I),),
    "hostsEvents": (
        re.compile(r"\b(?:upcoming|regular|weekly|one[- ]off) events?\b", re.I),
        re.compile(r"\bhosts? gigs?\b", re.I),
        re.compile(r"\bgigs? most nights?\b", re.I),
        re.compile(r"\bwhat['’]?s on (?:calendar|listings?)\b", re.I),
    ),
    "dogFriendly": (
        re.compile(r"\bdog[- ]friendly\b", re.I),
        re.compile(r"\bdogs (?:are )?welcome\b", re.I),
    ),
    "wheelchairAccessible": (
        re.compile(r"\bwheelchair (?:access|accessible)\b", re.I),
        re.compile(r"\bstep[- ]free access\b", re.I),
    ),
    "acceptsReservations": (
        re.compile(r"\bbook a table\b", re.I),
        re.compile(r"\breserve a table\b", re.I),
        re.compile(r"\btable bookings?\b", re.I),
    ),
    "suitableForGroups": (
        re.compile(r"\bgroup bookings?\b", re.I),
        re.compile(r"\bprivate hire\b", re.I),
    ),
    "hasHappyHour": (
        re.compile(r"\bhappy[- ]hour\b", re.I),
        re.compile(r"\b(?:two|2)[- ]for[- ](?:one|1) (?:drinks?|cocktails?)\b", re.I),
    ),
    "servesSundayRoast": (
        re.compile(r"\bsunday roasts?\b", re.I),
        re.compile(r"\broast dinners?\b", re.I),
    ),
    "hasVeganOptions": (
        re.compile(r"\bvegan (?:options?|menu|dishes?|food)\b", re.I),
        re.compile(r"\bplant[- ]based (?:options?|menu|dishes?|food)\b", re.I),
    ),
    "hostsQuiz": (
        re.compile(r"\b(?:pub |weekly )?quiz nights?\b", re.I),
        re.compile(r"\bweekly pub quiz\b", re.I),
        re.compile(r"\btrivia nights?\b", re.I),
    ),
    "hasAccessibleToilet": (
        re.compile(r"\baccessible toilets?\b", re.I),
        re.compile(r"\bdisabled toilets?\b", re.I),
        re.compile(r"\bwheelchair[- ]accessible toilets?\b", re.I),
    ),
}
PRIORITY_LINK_TERMS = (
    "access",
    "book",
    "dog",
    "event",
    "food",
    "hire",
    "happy",
    "menu",
    "music",
    "sport",
    "quiz",
    "roast",
    "vegan",
    "whatson",
    "what-s-on",
)


class FeatureCandidate(BaseModel):
    """One evidence-backed feature suggestion awaiting human review."""

    venueId: str
    venueName: str
    feature: str
    suggestedValue: bool = True
    pageUrl: str
    matchedPhrase: str
    evidenceText: str
    reviewStatus: str = "manual_review"


class ScanFailure(BaseModel):
    """A venue website that could not be scanned safely."""

    venueId: str
    venueName: str
    website: str | None
    reason: str


class WebsiteFeatureReport(BaseModel):
    """Auditable output from a bounded official-site scan."""

    scannedAt: datetime
    venuesRequested: int = Field(ge=0)
    venuesScanned: int = Field(ge=0)
    pagesFetched: int = Field(ge=0)
    candidates: list[FeatureCandidate]
    failures: list[ScanFailure]


class VisiblePageParser(HTMLParser):
    """Extract visible text and links without executing page code."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hiddenDepth = 0
        self.textParts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self.hiddenDepth += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self.hiddenDepth:
            self.hiddenDepth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hiddenDepth and data.strip():
            self.textParts.append(data)

    @property
    def visibleText(self) -> str:
        """Return whitespace-normalised visible page text."""
        return re.sub(r"\s+", " ", " ".join(self.textParts)).strip()


class OfficialWebsiteScanner:
    """Crawl a small same-site page set and emit review candidates."""

    def __init__(
        self,
        *,
        maxPagesPerVenue: int = 6,
        delaySeconds: float = 0.5,
        timeoutSeconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if maxPagesPerVenue < 1:
            raise ValueError("maxPagesPerVenue must be positive")
        self.maxPagesPerVenue = maxPagesPerVenue
        self.delaySeconds = delaySeconds
        self.timeoutSeconds = timeoutSeconds
        self.transport = transport

    def scan(self, venues: list[Venue]) -> WebsiteFeatureReport:
        """Scan venue websites without automatically changing canonical facts."""
        candidates: list[FeatureCandidate] = []
        failures: list[ScanFailure] = []
        pagesFetched = 0
        venuesScanned = 0
        with httpx.Client(
            follow_redirects=True,
            timeout=self.timeoutSeconds,
            transport=self.transport,
            headers={"User-Agent": SCANNER_USER_AGENT, "Accept": "text/html"},
        ) as client:
            for venue in venues:
                if not venue.website or not isPublicHttpUrl(venue.website):
                    failures.append(self._failure(venue, "missing or non-public website"))
                    continue
                try:
                    venueCandidates, venuePages = self._scanVenue(client, venue)
                except (httpx.HTTPError, ValueError) as error:
                    failures.append(self._failure(venue, str(error)))
                    continue
                candidates.extend(venueCandidates)
                pagesFetched += venuePages
                venuesScanned += 1
        return WebsiteFeatureReport(
            scannedAt=datetime.now(UTC),
            venuesRequested=len(venues),
            venuesScanned=venuesScanned,
            pagesFetched=pagesFetched,
            candidates=candidates,
            failures=failures,
        )

    def _scanVenue(self, client: httpx.Client, venue: Venue) -> tuple[list[FeatureCandidate], int]:
        baseUrl = venue.website or ""
        robotParser = self._robots(client, baseUrl)
        queue = deque([baseUrl])
        visited: set[str] = set()
        candidatesByFeatureValue: dict[tuple[str, bool], FeatureCandidate] = {}
        while queue and len(visited) < self.maxPagesPerVenue:
            pageUrl = urldefrag(queue.popleft()).url
            if pageUrl in visited or not isSameSite(baseUrl, pageUrl):
                continue
            if robotParser and not robotParser.can_fetch(SCANNER_USER_AGENT, pageUrl):
                continue
            if visited and self.delaySeconds:
                time.sleep(self.delaySeconds)
            response = client.get(pageUrl)
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                # A stale child link should not discard evidence already
                # collected from a healthy official homepage.
                if visited:
                    continue
                raise
            if "text/html" not in response.headers.get("content-type", "text/html"):
                continue
            if not isSameSite(baseUrl, str(response.url)):
                raise ValueError("website redirected outside its official host")
            visited.add(pageUrl)
            parser = VisiblePageParser()
            parser.feed(response.text)
            for candidate in findFeatureCandidates(venue, str(response.url), parser.visibleText):
                candidatesByFeatureValue.setdefault(
                    (candidate.feature, candidate.suggestedValue), candidate
                )
            discovered = {urldefrag(urljoin(str(response.url), href)).url for href in parser.links}
            queue.extend(
                sorted(
                    (url for url in discovered if isSameSite(baseUrl, url)),
                    key=linkPriority,
                )
            )
        return list(candidatesByFeatureValue.values()), len(visited)

    @staticmethod
    def _robots(client: httpx.Client, baseUrl: str) -> RobotFileParser | None:
        parts = urlsplit(baseUrl)
        robotsUrl = f"{parts.scheme}://{parts.netloc}/robots.txt"
        response = client.get(robotsUrl)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        parser = RobotFileParser()
        parser.set_url(robotsUrl)
        parser.parse(response.text.splitlines())
        return parser

    @staticmethod
    def _failure(venue: Venue, reason: str) -> ScanFailure:
        return ScanFailure(
            venueId=venue.venueId,
            venueName=venue.name,
            website=venue.website,
            reason=reason,
        )


def findFeatureCandidates(venue: Venue, pageUrl: str, visibleText: str) -> list[FeatureCandidate]:
    """Match conservative phrases and preserve a short evidence window."""
    candidatesByFeatureValue: dict[tuple[str, bool], FeatureCandidate] = {}
    for feature, patterns in FEATURE_PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(visibleText):
                suggestedValue = classifyMatch(feature, visibleText, match)
                if suggestedValue is None:
                    continue
                start = max(0, match.start() - 90)
                end = min(len(visibleText), match.end() + 90)
                candidatesByFeatureValue.setdefault(
                    (feature, suggestedValue),
                    FeatureCandidate(
                        venueId=venue.venueId,
                        venueName=venue.name,
                        feature=feature,
                        suggestedValue=suggestedValue,
                        pageUrl=pageUrl,
                        matchedPhrase=match.group(0),
                        evidenceText=visibleText[start:end],
                    ),
                )
    return list(candidatesByFeatureValue.values())


def classifyMatch(feature: str, visibleText: str, match: re.Match[str]) -> bool | None:
    """Classify clear affirmation/negation and ignore questions or assistance dogs."""
    before = visibleText[max(0, match.start() - 60) : match.start()]
    after = visibleText[match.end() : min(len(visibleText), match.end() + 40)]
    if "?" in after and not re.search(r"[.!?]", after.split("?", maxsplit=1)[0]):
        return None
    if feature == "dogFriendly" and re.search(r"\bassistance\s*$", before, re.I):
        return None
    negation = re.compile(
        r"(?:\b(?:cannot|can't|do not|don't|does not|doesn't|never)\b"
        r"(?:\s+\w+){0,7}|\b(?:no|not)\b(?:\s+\w+){0,2})\s*$",
        re.I,
    )
    return not bool(negation.search(before))


def isPublicHttpUrl(url: str) -> bool:
    """Reject local or non-HTTP scan targets."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    if parts.hostname.casefold() == "localhost":
        return False
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        return True
    return not (address.is_private or address.is_loopback or address.is_link_local)


def isSameSite(baseUrl: str, candidateUrl: str) -> bool:
    """Allow only HTTP links on the venue website's host."""
    base = urlsplit(baseUrl)
    candidate = urlsplit(candidateUrl)
    baseHost = (base.hostname or "").removeprefix("www.").casefold()
    candidateHost = (candidate.hostname or "").removeprefix("www.").casefold()
    return candidate.scheme in {"http", "https"} and candidateHost == baseHost


def linkPriority(url: str) -> tuple[int, str]:
    """Visit likely feature pages before generic navigation links."""
    compact = re.sub(r"[^a-z]", "", url.casefold())
    return (0 if any(term.replace("-", "") in compact for term in PRIORITY_LINK_TERMS) else 1, url)


def readSelectedVenueIds(path: Path) -> set[str]:
    """Read IDs from a JSON string list or audit object list."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("venue ID file must contain a JSON list")
    selectedIds: set[str] = set()
    for item in payload:
        if isinstance(item, str):
            selectedIds.add(item)
        elif isinstance(item, dict) and isinstance(item.get("venueId"), str):
            selectedIds.add(item["venueId"])
    return selectedIds


def main() -> int:
    """Scan official websites and write a manual-review report."""
    parser = argparse.ArgumentParser(description="Scan official venue sites for feature evidence")
    parser.add_argument("venuePath", type=Path)
    parser.add_argument("outputPath", type=Path)
    parser.add_argument("--venue-ids-file", type=Path)
    parser.add_argument("--pages-per-venue", type=int, default=6)
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    arguments = parser.parse_args()
    venues = TypeAdapter(list[Venue]).validate_json(arguments.venuePath.read_text(encoding="utf-8"))
    if arguments.venue_ids_file:
        selectedIds = readSelectedVenueIds(arguments.venue_ids_file)
        venues = [venue for venue in venues if venue.venueId in selectedIds]
    report = OfficialWebsiteScanner(
        maxPagesPerVenue=arguments.pages_per_venue,
        delaySeconds=arguments.delay_seconds,
    ).scan(venues)
    arguments.outputPath.parent.mkdir(parents=True, exist_ok=True)
    arguments.outputPath.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(
        f"Scanned {report.venuesScanned}/{report.venuesRequested} venues; "
        f"found {len(report.candidates)} review candidates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
