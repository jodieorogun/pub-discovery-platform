"""FSQ Places import and normalisation tests."""

import json
from pathlib import Path

import httpx
import pytest

from app.source_import.fsq_importer import (
    FSQ_API_VERSION,
    FSQ_SEARCH_FIELDS,
    FsqImportError,
    FsqPlacesClient,
    loadFsqApiKey,
    normaliseFsqPayload,
    writeFsqPlaces,
)


def fsqPayload() -> dict[str, object]:
    """Build a representative current FSQ search response."""
    return {
        "results": [
            {
                "fsq_place_id": "fsq-1",
                "name": "The Example Arms",
                "latitude": 51.501,
                "longitude": -0.126,
                "location": {
                    "address": "1 Example Street",
                    "postcode": "SW1A 1AA",
                },
                "website": "https://example.invalid",
                "tel": "+442071234567",
                "price": 2,
                "categories": [{"name": "Pub"}, {"unexpected": "ignored"}],
            }
        ]
    }


def testNormaliseFsqPayload() -> None:
    places = normaliseFsqPayload(fsqPayload())

    assert len(places) == 1
    assert places[0].fsqPlaceId == "fsq-1"
    assert places[0].postcode == "SW1A 1AA"
    assert places[0].categories == ["Pub"]
    assert places[0].priceTier == 2


@pytest.mark.parametrize("payload", [{}, {"results": "wrong"}])
def testNormaliseRejectsMissingResults(payload: object) -> None:
    with pytest.raises(FsqImportError, match="results list"):
        normaliseFsqPayload(payload)


def testNormaliseRejectsInvalidPlace() -> None:
    with pytest.raises(FsqImportError, match="invalid place"):
        normaliseFsqPayload({"results": [{"name": "Missing fields"}]})


def testClientSendsCurrentApiRequest() -> None:
    def handleRequest(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        assert request.headers["X-Places-Api-Version"] == FSQ_API_VERSION
        assert "near=Westminster%2C+London" in str(request.url)
        assert f"fields={FSQ_SEARCH_FIELDS.replace(',', '%2C')}" in str(request.url)
        return httpx.Response(200, json=fsqPayload())

    places = FsqPlacesClient(
        "secret", transport=httpx.MockTransport(handleRequest)
    ).search(
        "Westminster, London",
        "pub",
        10,
        "4bf58dd8d48988d11b941735",
    )

    assert [place.name for place in places] == ["The Example Arms"]


@pytest.mark.parametrize(
    ("near", "query", "limit", "message"),
    [
        ("", "pub", 10, "area"),
        ("Westminster", "", 10, "query or FSQ category"),
        ("Westminster", "pub", 51, "between 1 and 50"),
    ],
)
def testClientRejectsInvalidSearch(
    near: str, query: str, limit: int, message: str
) -> None:
    with pytest.raises(FsqImportError, match=message):
        FsqPlacesClient("secret").search(near, query, limit)


def testClientSupportsCategoryOnlySearch() -> None:
    def handleRequest(request: httpx.Request) -> httpx.Response:
        assert "query=" not in str(request.url)
        assert "fsq_category_ids=pub-category" in str(request.url)
        return httpx.Response(200, json=fsqPayload())

    places = FsqPlacesClient(
        "secret", transport=httpx.MockTransport(handleRequest)
    ).search("Westminster", categoryIds="pub-category")
    assert len(places) == 1


def testClientSupportsBoundedCategorySearch() -> None:
    def handleRequest(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert "ne=51.6%2C-0.1" in url
        assert "sw=51.5%2C-0.2" in url
        assert "near=" not in url
        return httpx.Response(200, json=fsqPayload())

    places = FsqPlacesClient(
        "secret", transport=httpx.MockTransport(handleRequest)
    ).searchBounds((51.6, -0.1), (51.5, -0.2), "pub-category")
    assert len(places) == 1


def testClientRetriesRateLimitThenSucceeds() -> None:
    attempts = 0
    delays: list[float] = []

    def handleRequest(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, json=fsqPayload())

    places = FsqPlacesClient(
        "secret",
        transport=httpx.MockTransport(handleRequest),
        sleeper=delays.append,
    ).searchBounds((51.6, -0.1), (51.5, -0.2), "pub-category")

    assert len(places) == 1
    assert attempts == 2
    assert delays == [2.0]


def testClientFallsBackWhenRetryAfterIsMalformed() -> None:
    attempts = 0
    delays: list[float] = []

    def handleRequest(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "not-seconds"})
        return httpx.Response(200, json=fsqPayload())

    places = FsqPlacesClient(
        "secret",
        transport=httpx.MockTransport(handleRequest),
        retryDelaySeconds=1.5,
        sleeper=delays.append,
    ).search("Westminster", "pub")

    assert len(places) == 1
    assert delays == [1.5]


@pytest.mark.parametrize(
    ("timeoutSeconds", "maxRetries", "retryDelaySeconds", "message"),
    [
        (0.0, 3, 1.0, "timeout"),
        (20.0, -1, 1.0, "retry count"),
        (20.0, 3, -1.0, "retry delay"),
    ],
)
def testClientRejectsInvalidConfiguration(
    timeoutSeconds: float,
    maxRetries: int,
    retryDelaySeconds: float,
    message: str,
) -> None:
    with pytest.raises(FsqImportError, match=message):
        FsqPlacesClient(
            "secret",
            timeoutSeconds=timeoutSeconds,
            maxRetries=maxRetries,
            retryDelaySeconds=retryDelaySeconds,
        )


def testClientReportsHttpFailure() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(401, request=request))
    with pytest.raises(FsqImportError, match="HTTP 401"):
        FsqPlacesClient("secret", transport=transport).search("Westminster", "pub")


def testLoadKeyUsesEnvironment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FSQ_API_KEY", "from-environment")
    assert loadFsqApiKey(tmp_path / "missing.env") == "from-environment"


def testLoadKeyAndWritePlaces(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FSQ_API_KEY", raising=False)
    envPath = tmp_path / ".env"
    envPath.write_text("FSQ_API_KEY=from-file\n", encoding="utf-8")
    assert loadFsqApiKey(envPath) == "from-file"

    outputPath = tmp_path / "nested" / "places.json"
    writeFsqPlaces(normaliseFsqPayload(fsqPayload()), outputPath)
    output = json.loads(outputPath.read_text(encoding="utf-8"))
    assert output[0]["fsqPlaceId"] == "fsq-1"


def testMissingKeyFails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("FSQ_API_KEY", raising=False)
    with pytest.raises(FsqImportError, match="missing"):
        loadFsqApiKey(tmp_path / ".env")
