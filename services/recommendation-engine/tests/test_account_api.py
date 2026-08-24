"""Local account, session, and pub diary API tests."""

import base64

from fastapi.testclient import TestClient


def _register(client: TestClient) -> dict[str, object]:
    response = client.post(
        "/account/register",
        json={
            "email": "pubfan@example.com",
            "password": "a-secure-local-password",
            "displayName": "Pub Fan",
        },
    )
    assert response.status_code == 201
    return response.json()  # type: ignore[no-any-return]


def testRegistersPersistsSessionAndLogsOut(client: TestClient) -> None:
    account = _register(client)

    assert account["displayName"] == "Pub Fan"
    assert "password" not in account
    assert client.get("/account/me").json()["email"] == "pubfan@example.com"

    logout = client.post("/account/logout")
    assert logout.status_code == 204
    assert client.get("/account/me").status_code == 401


def testRejectsDuplicateAccountAndInvalidLogin(client: TestClient) -> None:
    _register(client)

    duplicate = client.post(
        "/account/register",
        json={
            "email": "PUBFAN@example.com",
            "password": "another-password",
            "displayName": "Another Fan",
        },
    )
    assert duplicate.status_code == 409

    client.post("/account/logout")
    invalid = client.post(
        "/account/login",
        json={"email": "pubfan@example.com", "password": "wrong-password"},
    )
    assert invalid.status_code == 401


def testStoresLatestVenueRatingAndPersonalisesResults(client: TestClient) -> None:
    _register(client)

    first = client.put(
        "/account/ratings/venue-001", json={"beenHere": True, "rating": 5}
    )
    assert first.status_code == 200
    assert first.json()["rating"] == 5

    updated = client.put(
        "/account/ratings/venue-001", json={"beenHere": True, "rating": 4}
    )
    assert updated.status_code == 200
    ratings = client.get("/account/ratings").json()
    assert len(ratings) == 1
    assert ratings[0]["rating"] == 4

    recommendations = client.post(
        "/recommendations", json={"query": "pub", "limit": 20}
    ).json()
    assert recommendations["personalised"] is False
    rated = next(
        item for item in recommendations["recommendations"] if item["venueId"] == "venue-001"
    )
    assert rated["beenHere"] is True
    assert rated["userRating"] == 4


def testAllowsVisitedWithoutRatingAndRejectsUnknownVenue(client: TestClient) -> None:
    _register(client)

    visited = client.put(
        "/account/ratings/venue-001", json={"beenHere": True, "rating": None}
    )
    missing = client.put(
        "/account/ratings/missing", json={"beenHere": True, "rating": 5}
    )

    assert visited.status_code == 200
    assert visited.json()["rating"] is None
    assert missing.status_code == 404


def testValidatesAccountAndRatingInputs(client: TestClient) -> None:
    invalidAccount = client.post(
        "/account/register",
        json={"email": "not-an-email", "password": "short", "displayName": " "},
    )
    assert invalidAccount.status_code == 422

    _register(client)
    assert client.put("/account/ratings/venue-001", json={"rating": 6}).status_code == 422


def testPrivateDiaryStatsNoteAndPhoto(client: TestClient) -> None:
    _register(client)
    png = b"\x89PNG\r\n\x1a\n" + b"private-test-image"
    photo = "data:image/png;base64," + base64.b64encode(png).decode()

    saved = client.put(
        "/account/ratings/venue-001",
        json={
            "beenHere": True,
            "rating": 5,
            "privateNote": "  Great night with friends.  ",
            "photoDataUrl": photo,
        },
    )
    diary = client.get("/account/diary")
    stats = client.get("/account/stats")
    image = client.get("/account/diary/venue-001/photo")

    assert saved.status_code == 200
    assert diary.json()[0]["privateNote"] == "Great night with friends."
    assert diary.json()[0]["photoUrl"] == "/account/diary/venue-001/photo"
    assert stats.json()["visitedTotal"] == 1
    assert stats.json()["visitedThisWeek"] == 1
    assert stats.json()["averageRating"] == 5.0
    assert stats.json()["londonVisited"] == 1
    assert stats.json()["londonTotal"] == 15
    assert stats.json()["londonPercent"] == 6.7
    assert image.content == png
    assert image.headers["content-type"] == "image/png"
    assert image.headers["x-content-type-options"] == "nosniff"

    # A quick star-only update must not erase the private review or photo.
    client.put("/account/ratings/venue-001", json={"beenHere": True, "rating": 4})
    updated = client.get("/account/diary").json()[0]
    assert updated["privateNote"] == "Great night with friends."
    assert updated["photoUrl"] == "/account/diary/venue-001/photo"


def testDiaryRequiresOwnerAndRejectsFakeImage(client: TestClient) -> None:
    assert client.get("/account/diary").status_code == 401
    assert client.get("/account/stats").status_code == 401
    _register(client)

    fake = "data:image/png;base64," + base64.b64encode(b"not really an image").decode()
    response = client.put(
        "/account/ratings/venue-001",
        json={"beenHere": True, "rating": 3, "photoDataUrl": fake},
    )
    assert response.status_code == 400
    assert client.get("/account/diary/venue-001/photo").status_code == 404
