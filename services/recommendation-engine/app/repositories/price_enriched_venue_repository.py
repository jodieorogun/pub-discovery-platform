"""Overlay verified menu-price observations onto canonical venue records."""

from app.models.venue import Venue
from app.repositories.sqlite_price_observation_repository import SqlitePriceObservationRepository
from app.repositories.venue_repository import VenueRepository


class PriceEnrichedVenueRepository:
    def __init__(
        self, venueRepository: VenueRepository, priceObservations: SqlitePriceObservationRepository
    ) -> None:
        self.venueRepository = venueRepository
        self.priceObservations = priceObservations

    def listVenues(self) -> list[Venue]:
        observations = self.priceObservations.latestByVenue()
        return [
            venue.model_copy(
                update={
                    "priceLevel": observations[venue.venueId][0],
                    "guinnessPriceGbp": observations[venue.venueId][1],
                    "priceEvidenceUrl": observations[venue.venueId][2],
                }
            )
            if venue.venueId in observations
            else venue
            for venue in self.venueRepository.listVenues()
        ]
