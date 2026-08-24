"""Conservative Guinness pint price extraction tests."""

import httpx
import pytest

from app.models.provenance import AttributeProvenance
from app.models.venue import Venue
from app.source_enrichment.guinness_price_scanner import (
    GuinnessPriceScanner,
    findGuinnessPriceCandidate,
    guinnessPriceLevel,
)


def venue() -> Venue:
    return Venue(
        venueId="pub-1",
        name="Test Pub",
        latitude=51.5,
        longitude=-0.1,
        area="Camden",
        website="https://pub.example/",
        tags=[],
        attributeProvenance={
            "website": AttributeProvenance(
                source="Official venue website",
                sourceRecordId="https://pub.example/",
                verifiedAt="2026-08-13",
                confidence=1,
            )
        },
    )


def testAppliesExactLondonThresholds() -> None:
    assert guinnessPriceLevel(3.99) == "cheap"
    assert guinnessPriceLevel(4.0) == "moderate"
    assert guinnessPriceLevel(7.0) == "moderate"
    assert guinnessPriceLevel(7.01) == "expensive"


def testFindsOneClearDraughtGuinnessPintPrice() -> None:
    candidate = findGuinnessPriceCandidate(
        venue(),
        "https://pub.example/drinks",
        "Draught beers Guinness pint £6.20 Camden Hells pint £6.50",
    )

    assert candidate is not None
    assert candidate.observedPriceGbp == 6.2
    assert candidate.priceLevel == "moderate"
    assert candidate.evidenceUrl == "https://pub.example/drinks"


def testRejectsAmbiguousOrWrongServingPrices() -> None:
    assert (
        findGuinnessPriceCandidate(
            venue(), "https://pub.example/menu", "Guinness bottle 330ml £4.50"
        )
        is None
    )
    assert (
        findGuinnessPriceCandidate(
            venue(),
            "https://pub.example/menu",
            "Delirium £6.80 8.5% 2/3 pint Belgium Guinness £6.20 4.3% Ireland",
        )
        is None
    )
    assert (
        findGuinnessPriceCandidate(
            venue(), "https://pub.example/menu", "Guinness pint £4.50 / £6.50"
        )
        is None
    )
    assert (
        findGuinnessPriceCandidate(
            venue(), "https://pub.example/menu", "A perfect pint of Guinness"
        )
        is None
    )


def testScannerPrioritisesMenuAndCreatesVerifiedCandidate() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text='<a href="/about">About</a><a href="/drinks-menu">Drinks menu</a>',
            )
        if request.url.path == "/drinks-menu":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<main>Draught Guinness · Pint £7.25</main>",
            )
        return httpx.Response(200, headers={"content-type": "text/html"}, text="About us")

    report = GuinnessPriceScanner(
        maxPagesPerVenue=3,
        delaySeconds=0,
        transport=httpx.MockTransport(handler),
    ).scan([venue()])

    assert report.pagesFetched == 2
    assert len(report.candidates) == 1
    assert report.candidates[0].priceLevel == "expensive"


def testScannerFollowsDirectOfficialLinkToExternalMenuPdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "pub.example":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=(
                    '<a href="https://menus.cdn.example/test-pub-drinks-menu.pdf">Drinks menu</a>'
                ),
            )
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"pdf")

    monkeypatch.setattr(
        GuinnessPriceScanner,
        "_pdfText",
        staticmethod(lambda content: "Draught Guinness pint £6.40"),
    )
    report = GuinnessPriceScanner(
        maxPagesPerVenue=2,
        delaySeconds=0,
        transport=httpx.MockTransport(handler),
    ).scan([venue()])

    assert len(report.candidates) == 1
    assert report.candidates[0].observedPriceGbp == 6.4
    assert any("menus.cdn.example/test-pub-drinks-menu.pdf" in url for url in requested)


def testScannerRequiresVerifiedOfficialWebsite() -> None:
    unverified = venue().model_copy(update={"attributeProvenance": {}})
    report = GuinnessPriceScanner(
        delaySeconds=0,
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    ).scan([unverified])

    assert not report.candidates
    assert report.failures[0].reason == "website is not verified as official"


def testScannerRecordsMissingWebsiteAndNoMatch() -> None:
    missing = venue().model_copy(update={"website": None})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<main>Welcome to our pub with cask beer.</main>",
        )

    report = GuinnessPriceScanner(
        maxPagesPerVenue=1,
        delaySeconds=0,
        maxWorkers=1,
        transport=httpx.MockTransport(handler),
    ).scan([missing, venue()])

    assert report.venuesScanned == 1
    assert report.pagesFetched == 1
    assert {failure.reason for failure in report.failures} == {
        "missing or non-public website",
        "no unambiguous Guinness pint price",
    }


def testPdfSizeAndParsingFailuresAreSafe() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        GuinnessPriceScanner._pdfText(b"x" * 12_000_001)
    with pytest.raises(ValueError, match="could not be parsed"):
        GuinnessPriceScanner._pdfText(b"not a PDF")


def testMenuLinksSortAheadOfGenericLinks() -> None:
    assert GuinnessPriceScanner._menuPriority("https://pub.example/drinks.pdf")[0] == 0
    assert GuinnessPriceScanner._menuPriority("https://pub.example/about")[0] == 1
