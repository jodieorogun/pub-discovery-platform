"""Find exact draught Guinness pint prices on verified official pub sites."""

import argparse
import io
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from pydantic import BaseModel, Field, TypeAdapter
from pypdf import PdfReader

from app.models.venue import Venue
from app.source_enrichment.official_website_scanner import (
    SCANNER_USER_AGENT,
    VisiblePageParser,
    isPublicHttpUrl,
    isSameSite,
    readSelectedVenueIds,
)

MAX_PDF_BYTES = 12_000_000
GUINNESS_WINDOW = re.compile(r".{0,50}\bguinness\b.{0,65}", re.I)
PRICE = re.compile(r"£\s*(\d{1,2}(?:\.\d{1,2})?)")
PINT_SIGNAL = re.compile(r"\b(?:pint|pt|568\s*ml|draught|draft)\b", re.I)
EXCLUDED_SERVING = re.compile(
    r"(?:\b(?:half|1/2|2/3|bottle|can|250\s*ml|284\s*ml|330\s*ml|440\s*ml|0\.0)\b|[½⅔])",
    re.I,
)


def guinnessPriceLevel(priceGbp: float) -> str:
    """Apply the agreed London Guinness pint thresholds exactly."""
    if priceGbp < 4:
        return "cheap"
    if priceGbp > 7:
        return "expensive"
    return "moderate"


class GuinnessPriceCandidate(BaseModel):
    venueId: str
    venueName: str
    priceLevel: str
    observedItem: str = "Pint of draught Guinness"
    observedPriceGbp: float = Field(gt=0, le=30)
    evidenceUrl: str
    evidenceText: str


class GuinnessPriceFailure(BaseModel):
    venueId: str
    venueName: str
    website: str | None
    reason: str


class GuinnessPriceReport(BaseModel):
    scannedAt: datetime
    venuesRequested: int
    venuesScanned: int
    pagesFetched: int
    candidates: list[GuinnessPriceCandidate]
    failures: list[GuinnessPriceFailure]


def findGuinnessPriceCandidate(
    venue: Venue, evidenceUrl: str, text: str
) -> GuinnessPriceCandidate | None:
    """Require Guinness, a full-pint signal, and one nearby GBP price."""
    normalised = re.sub(r"\s+", " ", text)
    for match in GUINNESS_WINDOW.finditer(normalised):
        evidence = match.group(0).strip()
        if not PINT_SIGNAL.search(evidence) or EXCLUDED_SERVING.search(evidence):
            continue
        guinnessEnd = re.search(r"\bguinness\b", evidence, re.I)
        if guinnessEnd is None:
            continue
        afterProduct = evidence[guinnessEnd.end() :]
        priceMatch = PRICE.search(afterProduct)
        if priceMatch is None or priceMatch.start() > 35:
            continue
        # Slash-separated or immediately adjacent second prices are serving-size ambiguity.
        trailing = afterProduct[priceMatch.end() : priceMatch.end() + 18]
        if re.search(r"(?:/|or)\s*£?\s*\d", trailing, re.I):
            continue
        price = float(priceMatch.group(1))
        if price < 2 or price > 15:
            continue
        return GuinnessPriceCandidate(
            venueId=venue.venueId,
            venueName=venue.name,
            priceLevel=guinnessPriceLevel(price),
            observedPriceGbp=price,
            evidenceUrl=evidenceUrl,
            evidenceText=evidence,
        )
    return None


class GuinnessPriceScanner:
    """Crawl bounded official-site menu links without changing live prices."""

    def __init__(
        self,
        *,
        maxPagesPerVenue: int = 8,
        delaySeconds: float = 0.5,
        timeoutSeconds: float = 20,
        maxWorkers: int = 6,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.maxPagesPerVenue = maxPagesPerVenue
        self.delaySeconds = delaySeconds
        self.timeoutSeconds = timeoutSeconds
        self.maxWorkers = maxWorkers
        self.transport = transport

    def scan(self, venues: list[Venue]) -> GuinnessPriceReport:
        candidates: list[GuinnessPriceCandidate] = []
        failures: list[GuinnessPriceFailure] = []
        pagesFetched = 0
        venuesScanned = 0
        venuesByHost: dict[str, list[Venue]] = {}
        for venue in venues:
            host = (urlsplit(venue.website or "").hostname or venue.venueId).casefold()
            venuesByHost.setdefault(host, []).append(venue)

        def scanHost(
            hostVenues: list[Venue],
        ) -> tuple[list[GuinnessPriceCandidate], list[GuinnessPriceFailure], int, int]:
            hostCandidates: list[GuinnessPriceCandidate] = []
            hostFailures: list[GuinnessPriceFailure] = []
            hostPages = 0
            hostScanned = 0
            with httpx.Client(
                follow_redirects=True,
                timeout=self.timeoutSeconds,
                transport=self.transport,
                headers={"User-Agent": SCANNER_USER_AGENT},
            ) as client:
                for index, venue in enumerate(hostVenues):
                    if index and self.delaySeconds:
                        time.sleep(self.delaySeconds)
                    if not venue.website or not isPublicHttpUrl(venue.website):
                        hostFailures.append(self._failure(venue, "missing or non-public website"))
                        continue
                    provenance = venue.attributeProvenance.get("website")
                    if provenance is None or provenance.source != "Official venue website":
                        hostFailures.append(
                            self._failure(venue, "website is not verified as official")
                        )
                        continue
                    try:
                        candidate, fetched = self._scanVenue(client, venue)
                    except (httpx.HTTPError, ValueError) as error:
                        hostFailures.append(self._failure(venue, str(error)))
                        continue
                    hostScanned += 1
                    hostPages += fetched
                    if candidate:
                        hostCandidates.append(candidate)
                    else:
                        hostFailures.append(
                            self._failure(venue, "no unambiguous Guinness pint price")
                        )
            return hostCandidates, hostFailures, hostPages, hostScanned

        with ThreadPoolExecutor(max_workers=self.maxWorkers) as executor:
            futures = [executor.submit(scanHost, group) for group in venuesByHost.values()]
            for completed, future in enumerate(as_completed(futures), start=1):
                hostCandidates, hostFailures, hostPages, hostScanned = future.result()
                candidates.extend(hostCandidates)
                failures.extend(hostFailures)
                pagesFetched += hostPages
                venuesScanned += hostScanned
                print(
                    f"Scanned {completed}/{len(futures)} website hosts; "
                    f"{len(candidates)} Guinness prices found",
                    flush=True,
                )
        return GuinnessPriceReport(
            scannedAt=datetime.now(UTC),
            venuesRequested=len(venues),
            venuesScanned=venuesScanned,
            pagesFetched=pagesFetched,
            candidates=candidates,
            failures=failures,
        )

    def _scanVenue(
        self, client: httpx.Client, venue: Venue
    ) -> tuple[GuinnessPriceCandidate | None, int]:
        baseUrl = venue.website or ""
        robots = self._robots(client, baseUrl)
        queue = deque([baseUrl])
        # Official sites commonly host their menu PDF on a separate CDN. Only
        # follow a public, menu-named PDF linked directly from the official site.
        trustedExternalPdfs: set[str] = set()
        visited: set[str] = set()
        while queue and len(visited) < self.maxPagesPerVenue:
            pageUrl = urldefrag(queue.popleft()).url
            isOfficialSite = isSameSite(baseUrl, pageUrl)
            if pageUrl in visited or (not isOfficialSite and pageUrl not in trustedExternalPdfs):
                continue
            pageRobots = robots if isOfficialSite else self._externalRobots(client, pageUrl)
            if pageRobots and not pageRobots.can_fetch(SCANNER_USER_AGENT, pageUrl):
                continue
            if visited and self.delaySeconds:
                time.sleep(self.delaySeconds)
            response = client.get(pageUrl, headers={"Accept": "text/html,application/pdf"})
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError:
                if visited:
                    continue
                raise
            visited.add(pageUrl)
            contentType = response.headers.get("content-type", "").casefold()
            if "pdf" in contentType or pageUrl.casefold().endswith(".pdf"):
                text = self._pdfText(response.content)
                candidate = findGuinnessPriceCandidate(venue, str(response.url), text)
                if candidate:
                    return candidate, len(visited)
                continue
            if "html" not in contentType and contentType:
                continue
            parser = VisiblePageParser()
            parser.feed(response.text)
            candidate = findGuinnessPriceCandidate(venue, str(response.url), parser.visibleText)
            if candidate:
                return candidate, len(visited)
            links = {urldefrag(urljoin(str(response.url), href)).url for href in parser.links}
            trustedExternalPdfs.update(
                url for url in links if self._isExternalMenuPdf(baseUrl, url)
            )
            queue.extend(
                sorted(
                    (
                        url
                        for url in links
                        if isSameSite(baseUrl, url) or url in trustedExternalPdfs
                    ),
                    key=self._menuPriority,
                )
            )
        return None, len(visited)

    @staticmethod
    def _pdfText(content: bytes) -> str:
        if len(content) > MAX_PDF_BYTES:
            raise ValueError("menu PDF exceeds 12 MB limit")
        try:
            reader = PdfReader(io.BytesIO(content))
            return " ".join(page.extract_text() or "" for page in reader.pages[:30])
        except Exception as error:
            raise ValueError("menu PDF could not be parsed") from error

    @staticmethod
    def _menuPriority(url: str) -> tuple[int, str]:
        compact = re.sub(r"[^a-z]", "", url.casefold())
        priority = 0 if any(term in compact for term in ("drink", "menu", "bar", "pdf")) else 1
        return priority, url

    @staticmethod
    def _isExternalMenuPdf(baseUrl: str, url: str) -> bool:
        """Trust only a directly linked public PDF whose URL identifies it as a menu."""
        compact = re.sub(r"[^a-z]", "", urlsplit(url).path.casefold())
        return (
            not isSameSite(baseUrl, url)
            and isPublicHttpUrl(url)
            and urlsplit(url).path.casefold().endswith(".pdf")
            and any(term in compact for term in ("drink", "menu", "bar"))
        )

    @classmethod
    def _externalRobots(cls, client: httpx.Client, pageUrl: str) -> RobotFileParser | None:
        """Skip a CDN asset if its robots policy cannot be checked safely."""
        try:
            return cls._robots(client, pageUrl)
        except httpx.HTTPError:
            parser = RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
            return parser

    @staticmethod
    def _robots(client: httpx.Client, baseUrl: str) -> RobotFileParser | None:
        parts = urlsplit(baseUrl)
        response = client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser

    @staticmethod
    def _failure(venue: Venue, reason: str) -> GuinnessPriceFailure:
        return GuinnessPriceFailure(
            venueId=venue.venueId,
            venueName=venue.name,
            website=venue.website,
            reason=reason,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("venues", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--venue-ids-file", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--pages-per-venue", type=int, default=8)
    parser.add_argument("--delay-seconds", type=float, default=0.5)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    venues = TypeAdapter(list[Venue]).validate_json(args.venues.read_text(encoding="utf-8"))
    if args.venue_ids_file:
        selected = readSelectedVenueIds(args.venue_ids_file)
        venues = [venue for venue in venues if venue.venueId in selected]
    report = GuinnessPriceScanner(
        maxPagesPerVenue=args.pages_per_venue,
        delaySeconds=args.delay_seconds,
        maxWorkers=args.workers,
    ).scan(venues)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if args.database:
        from app.repositories.sqlite_price_observation_repository import (
            PriceObservationInput,
            SqlitePriceObservationRepository,
        )

        repository = SqlitePriceObservationRepository(args.database)
        for candidate in report.candidates:
            repository.publish(PriceObservationInput.model_validate(candidate.model_dump()))
    print(
        f"Scanned {report.venuesScanned}/{report.venuesRequested} venues and found "
        f"{len(report.candidates)} verified Guinness prices"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
