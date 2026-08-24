"""SQLite-backed canonical venue repository."""

import sqlite3
from pathlib import Path

from app.models.venue import Venue


class SqliteVenueRepository:
    """Read validated canonical venues from the shared application database."""

    def __init__(self, databasePath: Path) -> None:
        self.databasePath = databasePath
        with sqlite3.connect(databasePath) as connection:
            hasVenues = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'venues'"
            ).fetchone()
        if hasVenues is None:
            raise ValueError("Application database has no canonical venues: rebuild it")

    def listVenues(self) -> list[Venue]:
        """Load canonical rows in stable order without a separate JSON data file."""
        with sqlite3.connect(self.databasePath) as connection:
            rows = connection.execute(
                "SELECT venue_json FROM venues ORDER BY name_key, venue_id"
            ).fetchall()
        return [Venue.model_validate_json(str(row[0])) for row in rows]
