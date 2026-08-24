"""Manual fallback API tests for prices the scanner could not find."""

from fastapi.testclient import TestClient


def testListsMissingPubsAndPublishesDerivedPriceBand(client: TestClient) -> None:
    missing = client.get("/review/prices/missing")

    assert missing.status_code == 200
    assert any(item["venueId"] == "venue-001" for item in missing.json())

    created = client.post(
        "/review/prices",
        json={
            "venueId": "venue-001",
            "observedPriceGbp": 7.25,
            "evidenceUrl": "https://example.com/drinks-menu",
        },
    )

    assert created.status_code == 201
    assert created.json()["venueName"] == "The Lantern & Lock"
    assert created.json()["priceLevel"] == "expensive"
    assert all(
        item["venueId"] != "venue-001" for item in client.get("/review/prices/missing").json()
    )


def testRejectsUnknownPubAndInvalidPrice(client: TestClient) -> None:
    unknown = client.post(
        "/review/prices",
        json={
            "venueId": "missing-pub",
            "observedPriceGbp": 6.2,
            "evidenceUrl": "https://example.com/menu",
        },
    )
    invalid = client.post(
        "/review/prices",
        json={
            "venueId": "venue-001",
            "observedPriceGbp": 1.5,
            "evidenceUrl": "https://example.com/menu",
        },
    )

    assert unknown.status_code == 404
    assert invalid.status_code == 422
