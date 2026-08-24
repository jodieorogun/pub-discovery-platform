"""Venue repository contract."""

from typing import Protocol

from app.models.venue import Venue


class VenueRepository(Protocol):
    """Provide venue records independently of their storage mechanism."""

    def listVenues(self) -> list[Venue]:
        """Return all venues available for candidate retrieval."""
        ...
