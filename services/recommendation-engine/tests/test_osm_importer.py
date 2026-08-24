"""OSM Overpass import and normalisation tests."""

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from app.source_import.osm_importer import (
    OsmImportError,
    OsmPlacesClient,
    buildOverpassQuery,
    normaliseOsmPayload,
    writeOsmPlaces,
)


def osmPayload() -> dict[str, object]:
    """Build representative node and way results from Overpass."""
    return {
        "elements": [
            {
                "type": "node",
                "id": 101,
                "lat": 51.501,
                "lon": -0.126,
                "tags": {
                    "name": "The Example Arms",
                    "amenity": "pub",
                    "addr:housenumber": "1",
                    "addr:street": "Example Street",
                    "addr:postcode": "SW1A 1AA",
                    "contact:website": "https://example.invalid",
                    "contact:phone": "+442071234567",
                    "food": "yes",
                    "outdoor_seating": "patio",
                    "live_music": "yes",
                    "dog": "leashed",
                    "wheelchair": "yes",
                    "reservation": "recommended",
                },
            },
            {
                "type": "way",
                "id": 202,
                "center": {"lat": 51.502, "lon": -0.127},
                "tags": {
                    "name": "Second Pub",
                    "amenity": "pub",
                    "addr:full": "2 Complete Address, London",
                    "website": "https://second.example.invalid",
                    "food": "no",
                    "outdoor_seating": "no",
                    "music:live": "no",
                    "dog": "no",
                    "wheelchair": "limited",
                    "reservation": "no",
                },
            },
        ]
    }


def testNormaliseOsmPayload() -> None:
    places = normaliseOsmPayload(osmPayload())

    assert [place.osmElementId for place in places] == ["node/101", "way/202"]
    assert places[0].address == "1 Example Street"
    assert places[0].postcode == "SW1A 1AA"
    assert places[1].address == "2 Complete Address, London"
    assert places[1].longitude == -0.127
    assert places[0].servesFood is True
    assert places[0].hasOutdoorSeating is True
    assert places[1].servesFood is False
    assert places[1].hasOutdoorSeating is False
    assert places[0].hasLiveMusic is True
    assert places[0].dogFriendly is True
    assert places[0].wheelchairAccessible is True
    assert places[0].acceptsReservations is True
    assert places[1].hasLiveMusic is False
    assert places[1].dogFriendly is False
    # Limited access is not promoted to fully wheelchair accessible.
    assert places[1].wheelchairAccessible is None
    assert places[1].acceptsReservations is False


def testCuisineIsPositiveFoodEvidence() -> None:
    payload = osmPayload()
    elements = payload["elements"]
    assert isinstance(elements, list)
    firstElement = elements[0]
    assert isinstance(firstElement, dict)
    tags = firstElement["tags"]
    assert isinstance(tags, dict)
    tags.pop("food")
    tags["cuisine"] = "british"

    assert normaliseOsmPayload(payload)[0].servesFood is True


@pytest.mark.parametrize("payload", [{}, {"elements": "wrong"}])
def testNormaliseRejectsMissingElements(payload: object) -> None:
    with pytest.raises(OsmImportError, match="elements list"):
        normaliseOsmPayload(payload)


def testNormaliseSkipsUnnamedAndRejectsMissingCoordinates() -> None:
    assert normaliseOsmPayload({"elements": [{"type": "node", "id": 1}]}) == []
    with pytest.raises(OsmImportError, match="has no coordinates"):
        normaliseOsmPayload(
            {"elements": [{"type": "way", "id": 2, "tags": {"name": "No Centre"}}]}
        )


def testClientSendsBoundedOverpassQuery() -> None:
    def handleRequest(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"] == "application/json"
        assert request.headers["User-Agent"].startswith("ai-recommendation-engine/")
        form = parse_qs(request.content.decode())
        query = form["data"][0]
        assert 'area["boundary"="administrative"]' in query
        assert '["name"="City of Westminster"]' in query
        assert 'nwr["amenity"="pub"]' in query
        return httpx.Response(200, json=osmPayload())

    places = OsmPlacesClient(transport=httpx.MockTransport(handleRequest)).search(
        "City of Westminster"
    )

    assert len(places) == 2


@pytest.mark.parametrize(
    ("areaName", "amenity", "message"),
    [("", "pub", "area"), ("Westminster", "", "amenity")],
)
def testClientRejectsInvalidSearch(areaName: str, amenity: str, message: str) -> None:
    with pytest.raises(OsmImportError, match=message):
        OsmPlacesClient().search(areaName, amenity)


def testClientReportsHttpAndNetworkFailures() -> None:
    httpFailure = httpx.MockTransport(
        lambda request: httpx.Response(429, request=request)
    )
    with pytest.raises(OsmImportError, match="HTTP 429"):
        OsmPlacesClient(transport=httpFailure).search("Westminster")

    def failRequest(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(OsmImportError, match="network error"):
        OsmPlacesClient(transport=httpx.MockTransport(failRequest)).search("Westminster")


def testClientFallsBackAfterTransientFailure() -> None:
    requestCount = 0

    def handleRequest(request: httpx.Request) -> httpx.Response:
        nonlocal requestCount
        requestCount += 1
        if requestCount == 1:
            return httpx.Response(504, request=request)
        return httpx.Response(200, json=osmPayload())

    places = OsmPlacesClient(transport=httpx.MockTransport(handleRequest)).search("Westminster")
    assert requestCount == 2
    assert len(places) == 2


def testQueryEscapesValues() -> None:
    query = buildOverpassQuery('City "Centre"', "pub\\bar")
    assert 'City \\"Centre\\"' in query
    assert "pub\\\\bar" in query


def testWriteOsmPlaces(tmp_path: Path) -> None:
    outputPath = tmp_path / "nested" / "osm.json"
    writeOsmPlaces(normaliseOsmPayload(osmPayload()), outputPath)
    output = json.loads(outputPath.read_text(encoding="utf-8"))
    assert output[0]["osmElementId"] == "node/101"
