"""SQLite storage for exact, automatically verified menu-price observations."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field


class PriceObservationInput(BaseModel):
    venueId: str = Field(min_length=1, max_length=100)
    venueName: str = Field(min_length=1, max_length=200)
    priceLevel: Literal["cheap", "moderate", "expensive"]
    observedItem: Literal["Pint of draught Guinness"] = "Pint of draught Guinness"
    observedPriceGbp: float = Field(gt=0, le=30)
    evidenceUrl: str = Field(pattern=r"^https?://", max_length=1000)


class PriceObservation(PriceObservationInput):
    observationId: int
    observedAt: datetime


class SqlitePriceObservationRepository:
    """Persist grounded scanner findings without a manual review queue."""

    def __init__(self, databasePath: Path) -> None:
        self.databasePath = databasePath
        self.databasePath.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(databasePath) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS price_observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    venue_id TEXT NOT NULL,
                    venue_name TEXT NOT NULL,
                    price_level TEXT NOT NULL
                        CHECK(price_level IN ('cheap','moderate','expensive')),
                    observed_item TEXT NOT NULL,
                    observed_price_gbp REAL NOT NULL,
                    evidence_url TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    UNIQUE(venue_id, observed_item, observed_price_gbp, evidence_url)
                )
                """
            )

    def publish(self, item: PriceObservationInput) -> PriceObservation:
        """Store one strict match, making repeated scans idempotent."""
        now = datetime.now(UTC)
        with sqlite3.connect(self.databasePath) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO price_observations (
                    venue_id, venue_name, price_level, observed_item,
                    observed_price_gbp, evidence_url, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.venueId,
                    item.venueName,
                    item.priceLevel,
                    item.observedItem,
                    item.observedPriceGbp,
                    item.evidenceUrl,
                    now.isoformat(),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM price_observations
                WHERE venue_id = ? AND observed_item = ?
                  AND observed_price_gbp = ? AND evidence_url = ?
                """,
                (item.venueId, item.observedItem, item.observedPriceGbp, item.evidenceUrl),
            ).fetchone()
        if row is None:
            raise RuntimeError("Published price observation disappeared")
        return self._observation(row)

    def listObservations(self) -> list[PriceObservation]:
        with sqlite3.connect(self.databasePath) as connection:
            rows = connection.execute(
                "SELECT * FROM price_observations ORDER BY observation_id"
            ).fetchall()
        return [self._observation(row) for row in rows]

    def latestByVenue(self) -> dict[str, tuple[str, float, str]]:
        """Return the newest verified Guinness band, amount, and evidence per pub."""
        return {
            item.venueId: (item.priceLevel, item.observedPriceGbp, item.evidenceUrl)
            for item in self.listObservations()
            if item.observedItem == "Pint of draught Guinness"
        }

    @staticmethod
    def _observation(row: tuple[object, ...]) -> PriceObservation:
        return PriceObservation(
            observationId=int(cast(int, row[0])),
            venueId=str(row[1]),
            venueName=str(row[2]),
            priceLevel=cast(Literal["cheap", "moderate", "expensive"], str(row[3])),
            observedItem=str(row[4]),
            observedPriceGbp=float(cast(float, row[5])),
            evidenceUrl=str(row[6]),
            observedAt=datetime.fromisoformat(str(row[7])),
        )
