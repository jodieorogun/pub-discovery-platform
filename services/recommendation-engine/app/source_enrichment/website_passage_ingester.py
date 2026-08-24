"""Extract bounded, attributable RAG passages from official venue websites."""

import argparse
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
from app.source_enrichment.official_website_scanner import (
    PRIORITY_LINK_TERMS,
    SCANNER_USER_AGENT,
    isPublicHttpUrl,
    isSameSite,
    linkPriority,
    readSelectedVenueIds,
)

BLOCK_TAGS = {"article", "blockquote", "div", "h1", "h2", "h3", "h4", "li", "p", "section"}
IGNORED_TAGS = {
    "aside",
    "button",
    "footer",
    "form",
    "header",
    "nav",
    "noscript",
    "script",
    "style",
    "svg",
}
RELEVANT_TERMS = re.compile(
    r"\b(?:accessible|atmosphere|bar|beer|book|breakfast|brunch|cocktail|cosy|cozy|"
    r"dining|dinner|dj|dog|drink|event|family|food|football|garden|group|heritage|"
    r"historic|lively|lunch|menu|music|party|private hire|pub|quiet|reservation|"
    r"roast|rugby|seasonal|sport|sunday|terrace|traditional|view|wheelchair)\b",
    re.I,
)
NOISE_PATTERNS = (
    re.compile(r"\b(?:accept|manage|reject) (?:all )?cookies?\b", re.I),
    re.compile(r"\bprivacy (?:policy|preferences?)\b", re.I),
    re.compile(r"\ball rights reserved\b", re.I),
    re.compile(r"\bsign up (?:to|for) (?:our )?newsletter\b", re.I),
    re.compile(r"\bsign up (?:to|for) (?:our )?mailing list\b", re.I),
    re.compile(r"\bskip to (?:main )?content\b", re.I),
    re.compile(r"\bdownload (?:the|our) .{0,30}\bapp\b", re.I),
    re.compile(r"\b(?:offers and discounts|terms and conditions|up for grabs)\b", re.I),
    re.compile(r"\b(?:spin the wheel|skip the queue|gift cards?)\b", re.I),
    re.compile(r"\b(?:highly recommend|tripadvisor|customer reviews?)\b", re.I),
    re.compile(r"\b(?:five|[1-5])[- ]stars?\b", re.I),
    re.compile(
        r"\b(?:we|i) (?:stumbled|visited|came here|went there|had a brilliant time)\b",
        re.I,
    ),
    re.compile(r"\bwish i could\b", re.I),
)


class WebsitePassage(BaseModel):
    """One cleaned passage with enough provenance for retrieval evidence."""

    venueId: str
    venueName: str
    pageUrl: str
    text: str = Field(min_length=40, max_length=700)
    retrievedAt: datetime
    source: str = "Official venue website"


class PassageFailure(BaseModel):
    """A venue site that could not produce passages."""

    venueId: str
    venueName: str
    website: str | None
    reason: str


class WebsitePassageReport(BaseModel):
    """Auditable bounded website-ingestion output."""

    scannedAt: datetime
    venuesRequested: int = Field(ge=0)
    venuesScanned: int = Field(ge=0)
    pagesFetched: int = Field(ge=0)
    passages: list[WebsitePassage]
    failures: list[PassageFailure]


class PassageHtmlParser(HTMLParser):
    """Collect visible block text while excluding navigation and page chrome."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ignoredDepth = 0
        self.ignoredTags: list[str] = []
        self.currentParts: list[str] = []
        self.blocks: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        marker = " ".join(
            value or ""
            for key, value in attrs
            if key in {"aria-label", "class", "data-testid", "id"}
        )
        isReviewContainer = bool(
            re.search(r"\b(?:customer-review|review|testimonial|tripadvisor)s?\b", marker, re.I)
        )
        if tag in IGNORED_TAGS or isReviewContainer:
            self.ignoredDepth += 1
            self.ignoredTags.append(tag)
        if tag == "a" and not self.ignoredDepth:
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in BLOCK_TAGS and not self.ignoredDepth:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in BLOCK_TAGS and not self.ignoredDepth:
            self._flush()
        if self.ignoredTags and tag == self.ignoredTags[-1]:
            self.ignoredTags.pop()
            self.ignoredDepth -= 1

    def handle_data(self, data: str) -> None:
        if not self.ignoredDepth and data.strip():
            self.currentParts.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", " ".join(self.currentParts)).strip()
        if text:
            self.blocks.append(text)
        self.currentParts = []


def cleanPassages(blocks: list[str], venue: Venue, limit: int) -> list[str]:
    """Keep concise venue-relevant prose and remove repeated site chrome."""
    passages: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        text = re.sub(r"\s+", " ", block).strip(" -|•\t\n")
        if len(text) < 40 or any(pattern.search(text) for pattern in NOISE_PATTERNS):
            continue
        if text[-1] not in ".!?…" and len(text.split()) <= 15:
            continue
        if not RELEVANT_TERMS.search(text) and venue.name.casefold() not in text.casefold():
            continue
        for chunk in splitPassage(text):
            key = re.sub(r"[^a-z0-9]+", " ", chunk.casefold()).strip()
            if key in seen:
                continue
            seen.add(key)
            passages.append(chunk)
            if len(passages) >= limit:
                return passages
    return passages


def removeCrossVenueBoilerplate(passages: list[WebsitePassage]) -> list[WebsitePassage]:
    """Remove exact prose reused across multiple venues on operator templates."""
    venuesByText: dict[str, set[str]] = {}
    for passage in passages:
        key = re.sub(r"[^a-z0-9]+", " ", passage.text.casefold()).strip()
        venuesByText.setdefault(key, set()).add(passage.venueId)
    repeated = {key for key, venueIds in venuesByText.items() if len(venueIds) >= 2}
    return [
        passage
        for passage in passages
        if re.sub(r"[^a-z0-9]+", " ", passage.text.casefold()).strip()
        not in repeated
    ]


def splitPassage(text: str, maxLength: int = 700) -> list[str]:
    """Split oversized blocks at sentence boundaries without fabricating text."""
    if len(text) <= maxLength:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > maxLength:
            sentence = sentence[:maxLength].rsplit(" ", maxsplit=1)[0].rstrip(" ,;:") + "…"
        candidate = f"{current} {sentence}".strip()
        if current and len(candidate) > maxLength:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if len(chunk) >= 40]


class WebsitePassageIngester:
    """Crawl a bounded same-site page set and retain grounded prose passages."""

    def __init__(
        self,
        *,
        maxPagesPerVenue: int = 4,
        maxPassagesPerVenue: int = 20,
        delaySeconds: float = 0.5,
        timeoutSeconds: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if maxPagesPerVenue < 1 or maxPassagesPerVenue < 1:
            raise ValueError("page and passage limits must be positive")
        self.maxPagesPerVenue = maxPagesPerVenue
        self.maxPassagesPerVenue = maxPassagesPerVenue
        self.delaySeconds = delaySeconds
        self.timeoutSeconds = timeoutSeconds
        self.transport = transport

    def ingest(self, venues: list[Venue]) -> WebsitePassageReport:
        passages: list[WebsitePassage] = []
        failures: list[PassageFailure] = []
        pagesFetched = 0
        venuesScanned = 0
        scannedAt = datetime.now(UTC)
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
                websiteProvenance = venue.attributeProvenance.get("website")
                if (
                    websiteProvenance is None
                    or websiteProvenance.source != "Official venue website"
                ):
                    failures.append(
                        self._failure(venue, "website is not verified as official")
                    )
                    continue
                try:
                    venuePassages, venuePages = self._ingestVenue(client, venue, scannedAt)
                except (httpx.HTTPError, ValueError) as error:
                    failures.append(self._failure(venue, str(error)))
                    continue
                pagesFetched += venuePages
                venuesScanned += 1
                if venuePassages:
                    passages.extend(venuePassages)
                else:
                    failures.append(self._failure(venue, "no relevant passages found"))
        passages = removeCrossVenueBoilerplate(passages)
        return WebsitePassageReport(
            scannedAt=scannedAt,
            venuesRequested=len(venues),
            venuesScanned=venuesScanned,
            pagesFetched=pagesFetched,
            passages=passages,
            failures=failures,
        )

    def _ingestVenue(
        self, client: httpx.Client, venue: Venue, retrievedAt: datetime
    ) -> tuple[list[WebsitePassage], int]:
        baseUrl = venue.website or ""
        robotParser = self._robots(client, baseUrl)
        queue = deque([baseUrl])
        visited: set[str] = set()
        passages: list[WebsitePassage] = []
        seenText: set[str] = set()
        while (
            queue
            and len(visited) < self.maxPagesPerVenue
            and len(passages) < self.maxPassagesPerVenue
        ):
            pageUrl = urldefrag(queue.popleft()).url
            if pageUrl in visited or not isSameSite(baseUrl, pageUrl):
                continue
            if robotParser and not robotParser.can_fetch(SCANNER_USER_AGENT, pageUrl):
                continue
            if visited and self.delaySeconds:
                time.sleep(self.delaySeconds)
            response = client.get(pageUrl)
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", "text/html"):
                continue
            if not isSameSite(baseUrl, str(response.url)):
                raise ValueError("website redirected outside its official host")
            visited.add(pageUrl)
            parser = PassageHtmlParser()
            parser.feed(response.text)
            parser.close()
            remaining = self.maxPassagesPerVenue - len(passages)
            for text in cleanPassages(parser.blocks, venue, remaining):
                key = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
                if key in seenText:
                    continue
                seenText.add(key)
                passages.append(
                    WebsitePassage(
                        venueId=venue.venueId,
                        venueName=venue.name,
                        pageUrl=str(response.url),
                        text=text,
                        retrievedAt=retrievedAt,
                    )
                )
            discovered = {
                urldefrag(urljoin(str(response.url), href)).url for href in parser.links
            }
            queue.extend(
                sorted(
                    (
                        url
                        for url in discovered
                        if isSameSite(baseUrl, url) and self._isPriorityPage(url)
                    ),
                    key=linkPriority,
                )
            )
        return passages, len(visited)

    @staticmethod
    def _isPriorityPage(url: str) -> bool:
        path = urlsplit(url).path.casefold()
        compact = re.sub(r"[^a-z]", "", path)
        return path in {"", "/"} or any(
            term.replace("-", "") in compact for term in PRIORITY_LINK_TERMS
        )

    @staticmethod
    def _robots(client: httpx.Client, baseUrl: str) -> RobotFileParser | None:
        parts = urlsplit(baseUrl)
        response = client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        parser = RobotFileParser()
        parser.set_url(str(response.url))
        parser.parse(response.text.splitlines())
        return parser

    @staticmethod
    def _failure(venue: Venue, reason: str) -> PassageFailure:
        return PassageFailure(
            venueId=venue.venueId,
            venueName=venue.name,
            website=venue.website,
            reason=reason,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("venuePath", type=Path)
    parser.add_argument("outputPath", type=Path)
    parser.add_argument("--venue-ids-file", type=Path)
    parser.add_argument("--pages-per-venue", type=int, default=4)
    parser.add_argument("--passages-per-venue", type=int, default=20)
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    arguments = parser.parse_args()
    venues = TypeAdapter(list[Venue]).validate_json(
        arguments.venuePath.read_text(encoding="utf-8")
    )
    if arguments.venue_ids_file:
        selectedIds = readSelectedVenueIds(arguments.venue_ids_file)
        venues = [venue for venue in venues if venue.venueId in selectedIds]
    report = WebsitePassageIngester(
        maxPagesPerVenue=arguments.pages_per_venue,
        maxPassagesPerVenue=arguments.passages_per_venue,
        delaySeconds=arguments.delay_seconds,
    ).ingest(venues)
    arguments.outputPath.parent.mkdir(parents=True, exist_ok=True)
    arguments.outputPath.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "venuesScanned": report.venuesScanned,
                "venuesRequested": report.venuesRequested,
                "pagesFetched": report.pagesFetched,
                "passages": len(report.passages),
                "failures": len(report.failures),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
