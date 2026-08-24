"""Import auditable Guinness pint prices using multiple strict identity signals."""

import argparse
import html
import json
import math
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from pydantic import BaseModel, TypeAdapter

from app.models.venue import Venue
from app.repositories.sqlite_price_observation_repository import (
    PriceObservationInput,
    SqlitePriceObservationRepository,
)
from app.source_enrichment.guinness_price_scanner import guinnessPriceLevel

SOURCE_ROOT = "https://www.pint-prices.com"
BOROUGH_URLS = (
    f"{SOURCE_ROOT}/borough-results/Camden",
    f"{SOURCE_ROOT}/borough-results/Westminster",
)
POSTCODE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b", re.I)
PUBS_DATA_PREFIX = "var pubsData = "


class DirectoryPub(BaseModel):
    sourceName: str
    sourceAddress: str
    sourcePostcode: str | None
    latitude: float
    longitude: float
    website: str | None
    pints: list[tuple[str, float]]


class MatchedDirectoryPrice(BaseModel):
    venueId: str
    venueName: str
    observedItem: str
    priceGbp: float
    evidenceUrl: str
    matchMethod: str


class DirectoryImportReport(BaseModel):
    pubsFound: int
    exactMatches: int
    published: int
    matchMethods: dict[str, int]
    observations: list[MatchedDirectoryPrice]


def _normaliseName(value: str) -> str:
    """Make harmless pub-name variants such as '&' versus 'and' comparable."""
    value = html.unescape(value).casefold().replace("’", "'")
    value = re.sub(r"\([^)]*\)", " ", value)
    value = re.sub(r"\band\b", " ", value)
    value = re.sub(r"\b(?:the|jd wetherspoon|pub|kentish town)\b", " ", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def _namesCompatible(left: str, right: str) -> bool:
    leftName = _normaliseName(left)
    rightName = _normaliseName(right)
    return leftName == rightName or leftName in rightName or rightName in leftName


def _normalisePostcode(value: str | None) -> str | None:
    if not value:
        return None
    match = POSTCODE.search(value)
    return re.sub(r"\s+", "", match.group(1)).upper() if match else None


def _addressTokens(value: str | None) -> set[str]:
    text = html.unescape(value or "").casefold()
    replacements = {
        "street": "st",
        "road": "rd",
        "avenue": "ave",
        "terrace": "ter",
        "lane": "ln",
        "place": "pl",
        "square": "sq",
    }
    for source, replacement in replacements.items():
        text = text.replace(source, replacement)
    return set(re.findall(r"[a-z0-9]+", text)) - {
        "london",
        "uk",
        "greater",
        "england",
    }


def _websiteSignature(url: str | None) -> tuple[str, str] | None:
    if not url:
        return None
    value = url if "://" in url else f"https://{url}"
    parts = urlsplit(value)
    host = (parts.hostname or "").removeprefix("www.").casefold()
    path = re.sub(r"/(?:book|onlinebookingform).*", "", parts.path.casefold()).rstrip("/")
    return (host, path) if host else None


def _distanceMetres(venue: Venue, directoryPub: DirectoryPub) -> float:
    radius = 6_371_000
    firstLatitude = math.radians(venue.latitude)
    secondLatitude = math.radians(directoryPub.latitude)
    latitudeDelta = math.radians(directoryPub.latitude - venue.latitude)
    longitudeDelta = math.radians(directoryPub.longitude - venue.longitude)
    haversine = (
        math.sin(latitudeDelta / 2) ** 2
        + math.cos(firstLatitude) * math.cos(secondLatitude) * math.sin(longitudeDelta / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(haversine))


def parseDirectoryPage(source: str) -> list[DirectoryPub]:
    """Read the structured pub records embedded in one borough result page."""
    line = next(
        (line.strip() for line in source.splitlines() if line.strip().startswith(PUBS_DATA_PREFIX)),
        None,
    )
    if line is None or not line.endswith(";"):
        raise ValueError("directory page does not contain pubsData")
    payload = json.loads(line[len(PUBS_DATA_PREFIX) : -1])
    pubs: list[DirectoryPub] = []
    for raw in payload.values():
        pints = [
            (str(item[0]).strip(), float(item[1]))
            for item in raw.get("pints", [])
            if len(item) == 2 and 2 <= float(item[1]) <= 15
        ]
        if not pints or raw.get("latitude") is None or raw.get("longitude") is None:
            continue
        address = str(raw.get("address") or "")
        pubs.append(
            DirectoryPub(
                sourceName=str(raw.get("name") or "").strip(),
                sourceAddress=address,
                sourcePostcode=_normalisePostcode(address),
                latitude=float(raw["latitude"]),
                longitude=float(raw["longitude"]),
                website=str(raw.get("website") or "") or None,
                pints=pints,
            )
        )
    return pubs


def _matchingVenue(venues: list[Venue], directoryPub: DirectoryPub) -> tuple[Venue, str] | None:
    """Resolve a pub using the first unambiguous strong identity strategy."""
    compatible = [
        venue for venue in venues if _namesCompatible(venue.name, directoryPub.sourceName)
    ]

    postcodeMatches = [
        venue
        for venue in compatible
        if directoryPub.sourcePostcode
        and _normalisePostcode(venue.postcode) == directoryPub.sourcePostcode
    ]
    if len(postcodeMatches) == 1:
        return postcodeMatches[0], "name+postcode"

    distances = sorted(
        ((_distanceMetres(venue, directoryPub), venue) for venue in venues),
        key=lambda item: item[0],
    )
    nextDistance = distances[1][0] if len(distances) > 1 else float("inf")
    if distances[0][0] <= 35 and distances[0][1] in compatible and nextDistance > 75:
        return distances[0][1], "name+coordinates"

    sourceWebsite = _websiteSignature(directoryPub.website)
    websiteMatches: list[Venue] = []
    if sourceWebsite:
        for venue in compatible:
            venueWebsite = _websiteSignature(venue.website)
            if not venueWebsite or sourceWebsite[0] != venueWebsite[0]:
                continue
            sourcePath, venuePath = sourceWebsite[1], venueWebsite[1]
            samePath = sourcePath == venuePath or sourcePath in venuePath or venuePath in sourcePath
            if samePath and (sourcePath or venuePath or len(compatible) == 1):
                websiteMatches.append(venue)
    if len(websiteMatches) == 1:
        return websiteMatches[0], "name+website"

    sourceTokens = _addressTokens(directoryPub.sourceAddress)
    addressMatches = []
    for venue in compatible:
        shared = sourceTokens & _addressTokens(venue.address)
        if len(shared) >= 2 and any(token.isdigit() for token in shared):
            addressMatches.append(venue)
    if len(addressMatches) == 1:
        return addressMatches[0], "name+address"
    return None


def _guinnessPrice(directoryPub: DirectoryPub) -> float | None:
    """Return only a Guinness price; other beers are not comparable substitutes."""
    guinnessPrices = [
        pint for pint in directoryPub.pints if pint[0].casefold() in {"guinness", "guiness"}
    ]
    return min((pint[1] for pint in guinnessPrices), default=None)


def matchDirectoryPrices(
    venues: list[Venue], pubs: list[DirectoryPub]
) -> list[MatchedDirectoryPrice]:
    """Return one Guinness price for every safely resolved canonical pub."""
    matches: dict[str, MatchedDirectoryPrice] = {}
    for directoryPub in pubs:
        price = _guinnessPrice(directoryPub)
        if price is None:
            continue
        identity = _matchingVenue(venues, directoryPub)
        if identity is None:
            continue
        venue, method = identity
        evidenceUrl = (
            f"{SOURCE_ROOT}/pub/{quote(directoryPub.sourceAddress, safe='')}/"
            f"{quote(directoryPub.sourceName, safe='')}"
        )
        candidate = MatchedDirectoryPrice(
            venueId=venue.venueId,
            venueName=venue.name,
            observedItem="Pint of draught Guinness",
            priceGbp=price,
            evidenceUrl=evidenceUrl,
            matchMethod=method,
        )
        current = matches.get(venue.venueId)
        if current is None or candidate.priceGbp < current.priceGbp:
            matches[venue.venueId] = candidate
    return sorted(matches.values(), key=lambda item: item.venueName.casefold())


def main() -> int:  # pragma: no cover - network CLI is exercised by live audit runs.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("venues", type=Path)
    parser.add_argument("database", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    venues = TypeAdapter(list[Venue]).validate_json(args.venues.read_text(encoding="utf-8"))
    pubs: list[DirectoryPub] = []
    with httpx.Client(follow_redirects=True, timeout=20) as client:
        for pageUrl in BOROUGH_URLS:
            response = client.get(pageUrl)
            response.raise_for_status()
            pubs.extend(parseDirectoryPage(response.text))
    matches = matchDirectoryPrices(venues, pubs)
    repository = SqlitePriceObservationRepository(args.database)
    for item in matches:
        repository.publish(
            PriceObservationInput(
                venueId=item.venueId,
                venueName=item.venueName,
                priceLevel=guinnessPriceLevel(item.priceGbp),
                observedItem=item.observedItem,
                observedPriceGbp=item.priceGbp,
                evidenceUrl=item.evidenceUrl,
            )
        )
    methodCounts = {
        method: sum(item.matchMethod == method for item in matches)
        for method in sorted({item.matchMethod for item in matches})
    }
    report = DirectoryImportReport(
        pubsFound=len(pubs),
        exactMatches=len(matches),
        published=len(matches),
        matchMethods=methodCounts,
        observations=matches,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"Published {len(matches)} exact matches from {len(pubs)} directory pub records")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
