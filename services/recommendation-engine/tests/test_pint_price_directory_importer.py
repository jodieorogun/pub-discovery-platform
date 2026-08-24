"""Strict secondary-source Guinness matching tests."""

import json

from app.models.venue import Venue
from app.source_enrichment.pint_price_directory_importer import (
    matchDirectoryPrices,
    parseDirectoryPage,
)


def venue(
    name: str = "The Ice Wharf",
    postcode: str | None = "NW1 7BY",
    *,
    venueId: str = "pub-1",
    latitude: float = 51.541,
    longitude: float = -0.146,
    address: str = "28B Jamestown Road",
    website: str | None = None,
) -> Venue:
    return Venue(
        venueId=venueId,
        name=name,
        latitude=latitude,
        longitude=longitude,
        area="Camden",
        address=address,
        postcode=postcode,
        website=website,
        tags=[],
    )


def directoryPage(*pubs: dict[str, object]) -> str:
    payload = {f"pub-{index}": pub for index, pub in enumerate(pubs)}
    return f"<script>\nvar pubsData = {json.dumps(payload)};\n</script>"


def rawPub(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "name": "The Ice Wharf - JD Wetherspoon",
        "address": "28A, 28B Jamestown Rd, London NW1 7BY",
        "latitude": 51.541,
        "longitude": -0.146,
        "website": "https://www.jdwetherspoon.com/pubs/the-ice-wharf-camden",
        "pints": [["Guinness", "5.07"], ["Carling", "5.29"]],
    }
    value.update(updates)
    return value


def testParsesStructuredDirectoryPubs() -> None:
    pubs = parseDirectoryPage(directoryPage(rawPub()))

    assert len(pubs) == 1
    assert pubs[0].sourcePostcode == "NW17BY"
    assert pubs[0].pints == [("Guinness", 5.07), ("Carling", 5.29)]


def testPrefersGuinnessAndMatchesExactPostcode() -> None:
    pubs = parseDirectoryPage(directoryPage(rawPub()))
    matches = matchDirectoryPrices([venue(), venue("Camden Head", "NW1 0LU")], pubs)

    assert len(matches) == 1
    assert matches[0].matchMethod == "name+postcode"
    assert matches[0].observedItem == "Pint of draught Guinness"
    assert matches[0].priceGbp == 5.07


def testRejectsOtherPintsWhenGuinnessIsMissing() -> None:
    pubs = parseDirectoryPage(directoryPage(rawPub(pints=[["Peroni", "7.10"], ["Amstel", "5.90"]])))

    matches = matchDirectoryPrices([venue()], pubs)

    assert matches == []


def testRejectsSameNameAtWrongLocation() -> None:
    pubs = parseDirectoryPage(
        directoryPage(
            rawPub(
                address="2 Camden Walk N1 8DY",
                latitude=51.5356,
                longitude=-0.1029,
                website="https://example.com/other-pub",
            )
        )
    )

    assert not matchDirectoryPrices([venue("The Ice Wharf", "NW1 0LU")], pubs)


def testSupportsCoordinatesWebsiteAndAddressAsIndependentStrongSignals() -> None:
    coordinatePub = rawPub(address="No postcode", website="", pints=[["Guinness", "6.00"]])
    websitePub = rawPub(
        name="Dog & Duck",
        address="Unknown",
        latitude=51.7,
        longitude=-0.3,
        website="https://pub.example/soho",
        pints=[["Guinness", "6.05"]],
    )
    addressPub = rawPub(
        name="Coach & Horse (Mayfair)",
        address="5 Bruton Street",
        latitude=51.8,
        longitude=-0.4,
        website="",
        pints=[["Guinness", "6.55"]],
    )
    venues = [
        venue(postcode=None),
        venue(
            "Dog and Duck",
            None,
            venueId="pub-2",
            latitude=51.6,
            longitude=-0.2,
            address="Unrecorded",
            website="https://pub.example/soho",
        ),
        venue(
            "The Coach and Horses",
            None,
            venueId="pub-3",
            latitude=51.6,
            longitude=-0.2,
            address="5 Bruton St",
        ),
    ]

    matches = matchDirectoryPrices(
        venues, parseDirectoryPage(directoryPage(coordinatePub, websitePub, addressPub))
    )

    assert {item.matchMethod for item in matches} == {
        "name+coordinates",
        "name+website",
        "name+address",
    }
