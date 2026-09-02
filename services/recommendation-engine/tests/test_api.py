"""API contract tests."""

from fastapi.testclient import TestClient


def testHealthEndpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def testValidRecommendationRequest(client: TestClient) -> None:
    response = client.post(
        "/recommendations",
        json={"query": "cheap pub near Waterloo with food and outdoor seating", "limit": 5},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["parsedPreferences"] == {
        "location": "Waterloo",
        "postcode": None,
        "excludedLocations": [],
        "priceLevel": "cheap",
        "excludedPriceLevels": [],
        "requiresFood": True,
        "requiresOutdoorSeating": True,
        "excludesOutdoorSeating": False,
        "showsSports": False,
        "requiresLiveMusic": False,
        "requiresDj": False,
        "requiresEvents": False,
        "requiresDogFriendly": False,
        "requiresWheelchairAccess": False,
        "requiresReservations": False,
        "suitableForGroups": False,
        "requiresHappyHour": False,
        "requiresSundayRoast": False,
        "requiresVeganOptions": False,
        "requiresQuizNight": False,
        "requiresAccessibleToilet": False,
        "groupSize": None,
        "noiseLevel": None,
        "preferredFeatures": [],
        "excludedFeatures": [],
        "contradictions": [],
        "unparsedTerms": [],
    }
    assert body["recommendations"][0]["venueId"] == "venue-001"
    assert body["recommendations"][0]["distanceKm"] is not None
    assert body["requestId"]
    assert body["parserVersion"] == "rules-v5-postcodes"
    assert body["rankingVersion"] == "weighted-v3-five-features+personal-v2"
    assert len(body["datasetVersion"]) == 12
    assert body["recommendations"][0]["scoreBreakdown"]
    assert body["offset"] == 0
    assert body["limit"] == 5
    assert body["totalAvailable"] >= len(body["recommendations"])
    assert body["hasMore"] is (body["totalAvailable"] > 5)


def testReadinessEndpointReportsLoadedData(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["venueCount"] == 15
    assert response.json()["datasetVersion"]
    assert response.json()["evidenceBackend"] is None


def testEmptyQueryRejected(client: TestClient) -> None:
    response = client.post("/recommendations", json={"query": "   ", "limit": 5})
    assert response.status_code == 422


def testLimitValidation(client: TestClient) -> None:
    assert client.post("/recommendations", json={"query": "pub", "limit": 0}).status_code == 422
    assert client.post("/recommendations", json={"query": "pub", "limit": 21}).status_code == 422
    assert (
        client.post(
            "/recommendations", json={"query": "pub", "limit": 5, "offset": -1}
        ).status_code
        == 422
    )


def testRecommendationPaginationDoesNotRepeatResults(client: TestClient) -> None:
    first = client.post(
        "/recommendations", json={"query": "pub", "limit": 5, "offset": 0}
    ).json()
    second = client.post(
        "/recommendations", json={"query": "pub", "limit": 5, "offset": 5}
    ).json()

    firstIds = {venue["venueId"] for venue in first["recommendations"]}
    secondIds = {venue["venueId"] for venue in second["recommendations"]}
    assert len(firstIds) == 5
    assert len(secondIds) == 5
    assert firstIds.isdisjoint(secondIds)
    assert first["totalAvailable"] == second["totalAvailable"]
    assert second["offset"] == 5


def testReportsUnparsedTermsWithoutSilentlyUsingThem(client: TestClient) -> None:
    response = client.post("/recommendations", json={"query": "rooftop pub in Waterloo"})
    body = response.json()

    assert body["ignoredPreferences"] == ["unparsed:rooftop"]
    assert body["warnings"] == [
        "Some query terms were not understood: rooftop",
        "Some preferences could not affect ranking because data was unavailable.",
    ]


def testExplainsContradictoryNoResultQuery(client: TestClient) -> None:
    response = client.post(
        "/recommendations", json={"query": "pub with sports but no sports"}
    )
    body = response.json()

    assert body["recommendations"] == []
    assert body["parsedPreferences"]["contradictions"] == ["sports"]
    assert body["noResultReasons"] == [
        "Conflicting requirements must be resolved before matching venues."
    ]
