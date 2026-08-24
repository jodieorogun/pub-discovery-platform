"""Automatic exact Guinness observation storage and venue overlay tests."""

from pathlib import Path

from app.models.venue import Venue
from app.repositories.price_enriched_venue_repository import PriceEnrichedVenueRepository
from app.repositories.sqlite_price_observation_repository import (
    PriceObservationInput,
    SqlitePriceObservationRepository,
)


class VenueRepo:
    def listVenues(self) -> list[Venue]:
        return [
            Venue(
                venueId="pub-1",
                name="Pub One",
                latitude=51.5,
                longitude=-0.1,
                area="Camden",
                tags=[],
            )
        ]


def testPublishesExactObservationAndOverlaysVenue(tmp_path: Path) -> None:
    prices = SqlitePriceObservationRepository(tmp_path / "prices.sqlite3")
    item = PriceObservationInput(
        venueId="pub-1",
        venueName="Pub One",
        priceLevel="moderate",
        observedItem="Pint of draught Guinness",
        observedPriceGbp=6.2,
        evidenceUrl="https://example.com/drinks",
    )

    published = prices.publish(item)
    republished = prices.publish(item)
    venue = PriceEnrichedVenueRepository(VenueRepo(), prices).listVenues()[0]

    assert republished.observationId == published.observationId
    assert len(prices.listObservations()) == 1
    assert venue.priceLevel == "moderate"
    assert venue.guinnessPriceGbp == 6.2
    assert venue.priceEvidenceUrl == "https://example.com/drinks"
