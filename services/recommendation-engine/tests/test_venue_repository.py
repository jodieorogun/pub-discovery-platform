"""Venue repository contract and implementation tests."""

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.venue import Venue
from app.repositories.json_venue_repository import JsonVenueRepository
from app.repositories.sqlite_venue_repository import SqliteVenueRepository
from app.repositories.venue_repository import VenueRepository
from app.schemas.recommendation import RecommendationRequest
from app.services.recommendation_service import RecommendationService


def makeVenue() -> Venue:
    """Build a valid venue for repository contract tests."""
    return Venue(
        venueId="venue-test",
        name="The Repository Arms",
        latitude=51.5033,
        longitude=-0.1147,
        area="Waterloo",
        priceLevel="cheap",
        servesFood=True,
        hasOutdoorSeating=True,
        showsSports=False,
        suitableForGroups=True,
        noiseLevel="quiet",
        rating=4.5,
        popularityScore=0.8,
        tags=["test"],
    )


class StubVenueRepository:
    """Minimal test implementation of the repository protocol."""

    def __init__(self, venues: list[Venue]) -> None:
        self.venues = venues

    def listVenues(self) -> list[Venue]:
        return list(self.venues)


def testRecommendationServiceAcceptsRepositoryProtocol() -> None:
    repository: VenueRepository = StubVenueRepository([makeVenue()])
    service = RecommendationService(repository)

    response = service.recommend(
        RecommendationRequest(query="cheap pub in Waterloo with food", limit=5)
    )

    assert [item.venueId for item in response.recommendations] == ["venue-test"]


def testJsonRepositoryLoadsValidatedVenues(tmp_path: Path) -> None:
    dataPath = tmp_path / "venues.json"
    dataPath.write_text(json.dumps([makeVenue().model_dump()]), encoding="utf-8")
    repository = JsonVenueRepository(dataPath)

    firstResult = repository.listVenues()
    firstResult.clear()

    assert repository.listVenues() == [makeVenue()]


def testJsonRepositoryRejectsInvalidVenueData(tmp_path: Path) -> None:
    dataPath = tmp_path / "venues.json"
    dataPath.write_text('[{"venueId": "incomplete"}]', encoding="utf-8")

    with pytest.raises(ValidationError):
        JsonVenueRepository(dataPath)


def testSqliteRepositoryLoadsValidatedVenues(tmp_path: Path) -> None:
    databasePath = tmp_path / "app.sqlite3"
    venue = makeVenue()
    with sqlite3.connect(databasePath) as connection:
        connection.execute(
            """
            CREATE TABLE venues (
                venue_id TEXT PRIMARY KEY,
                name_key TEXT NOT NULL,
                area TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                venue_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO venues VALUES (?, ?, ?, ?, ?, ?)",
            (
                venue.venueId,
                venue.name.casefold(),
                venue.area,
                venue.latitude,
                venue.longitude,
                venue.model_dump_json(),
            ),
        )

    assert SqliteVenueRepository(databasePath).listVenues() == [venue]


def testSqliteRepositoryRequiresVenueTable(tmp_path: Path) -> None:
    databasePath = tmp_path / "empty.sqlite3"
    sqlite3.connect(databasePath).close()

    with pytest.raises(ValueError, match="no canonical venues"):
        SqliteVenueRepository(databasePath)
