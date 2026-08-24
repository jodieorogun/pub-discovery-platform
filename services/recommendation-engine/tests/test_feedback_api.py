"""Recommendation feedback API tests."""

from fastapi.testclient import TestClient


def testRecordsFeedbackForKnownVenue(client: TestClient) -> None:
    response = client.post(
        "/feedback",
        json={
            "venueId": "venue-001",
            "eventType": "save",
            "query": "  pub in Waterloo  ",
            "recommendationRank": 1,
            "sessionId": "anonymous-session",
            "requestId": "request-1",
            "rankingVersion": "weighted-v2",
            "datasetVersion": "dataset-1",
        },
    )

    body = response.json()
    assert response.status_code == 201
    assert body["venueId"] == "venue-001"
    assert body["eventType"] == "save"
    assert body["query"] == "pub in Waterloo"
    assert body["eventId"]
    assert body["recordedAt"]
    assert body["requestId"] == "request-1"
    assert body["rankingVersion"] == "weighted-v2"
    assert body["datasetVersion"] == "dataset-1"


def testRejectsUnknownVenue(client: TestClient) -> None:
    response = client.post(
        "/feedback",
        json={"venueId": "missing", "eventType": "click"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown venueId: missing"


def testValidatesFeedbackRequest(client: TestClient) -> None:
    response = client.post(
        "/feedback",
        json={"venueId": " ", "eventType": "invented", "recommendationRank": 0},
    )

    assert response.status_code == 422
