"""Local recommendation demo tests."""

from fastapi.testclient import TestClient


def testServesDemoPage(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "London Pub Finder" in response.text
    assert 'id="search-form"' in response.text
    assert 'id="load-more"' in response.text
    assert "/assets/app.js" in response.text
    assert "leaflet@1.9.4" in response.text
    assert "Discover pubs" in response.text
    assert 'id="account-dialog"' in response.text
    assert "Create account" in response.text
    assert 'id="london-stat"' in response.text
    assert "fits the plan" not in response.text


def testServesDemoAssets(client: TestClient) -> None:
    styles = client.get("/assets/app.css")
    script = client.get("/assets/app.js")

    assert styles.status_code == 200
    assert styles.headers["content-type"].startswith("text/css")
    assert script.status_code == 200
    assert "recordFeedback" in script.text
    assert "PAGE_SIZE = 5" in script.text
    assert "payload.hasMore" in script.text
    assert "More like this" in script.text
    assert "Less like this" in script.text
    assert "renderMap" in script.text
    assert "tile.openstreetmap.org" in script.text
    assert "venueMarkerLayer" in script.text
    assert "Show in list" in script.text
    assert "unknownFeatures" in script.text
    assert "star-rating" in script.text
    assert "/account/ratings/" in script.text
    assert "personalReason" in script.text
    assert "loadAccount().then" in script.text
    assert "Hello, ${currentAccount.displayName}" in script.text
    assert "Community-reported price · open evidence" in script.text
    assert "Official-menu price · open evidence" in script.text


def testServesFeatureReviewPage(client: TestClient) -> None:
    page = client.get("/review")
    script = client.get("/assets/review.js")

    assert page.status_code == 200
    assert "Review official-site facts" in page.text
    assert script.status_code == 200
    assert "/review/features" in script.text


def testServesPrivateDiaryPage(client: TestClient) -> None:
    page = client.get("/diary")
    script = client.get("/assets/diary.js")

    assert page.status_code == 200
    assert "My pub diary" in page.text
    assert "Private to you" in page.text
    assert script.status_code == 200
    assert "/account/diary" in script.text
    assert "privateNote" in script.text
    assert "photoDataUrl" in script.text


def testReviewPageIncludesMissingPriceFallback(client: TestClient) -> None:
    page = client.get("/review")
    script = client.get("/assets/review.js")

    assert "Add a price the scanner missed" in page.text
    assert "Price review queue" not in page.text
    assert "/review/prices/missing" in script.text
    assert "priceLevel" not in page.text
