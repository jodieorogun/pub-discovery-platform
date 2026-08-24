"""Manual fallback for Guinness prices the official-site scan could not find."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.repositories.sqlite_price_observation_repository import (
    PriceObservation,
    PriceObservationInput,
    SqlitePriceObservationRepository,
)
from app.repositories.venue_repository import VenueRepository
from app.source_enrichment.guinness_price_scanner import guinnessPriceLevel

router = APIRouter(prefix="/review/prices", tags=["price-observations"])


class MissingPriceVenue(BaseModel):
    venueId: str
    name: str
    area: str
    website: str | None


class ManualPriceInput(BaseModel):
    venueId: str = Field(min_length=1, max_length=100)
    observedPriceGbp: float = Field(ge=2, le=15)
    evidenceUrl: str = Field(pattern=r"^https?://", max_length=1000)


def getPriceRepository(request: Request) -> SqlitePriceObservationRepository:
    return request.app.state.priceObservationRepository  # type: ignore[no-any-return]


def getVenueRepository(request: Request) -> VenueRepository:
    return request.app.state.venueRepository  # type: ignore[no-any-return]


@router.get("/missing", response_model=list[MissingPriceVenue])
def listMissingPriceVenues(
    prices: Annotated[SqlitePriceObservationRepository, Depends(getPriceRepository)],
    venues: Annotated[VenueRepository, Depends(getVenueRepository)],
) -> list[MissingPriceVenue]:
    """List only pubs for which neither the scanner nor a person found a price."""
    coveredVenueIds = prices.latestByVenue()
    return sorted(
        (
            MissingPriceVenue(
                venueId=venue.venueId,
                name=venue.name,
                area=venue.area,
                website=venue.website,
            )
            for venue in venues.listVenues()
            if venue.venueId not in coveredVenueIds
        ),
        key=lambda venue: (venue.area.casefold(), venue.name.casefold()),
    )


@router.post("", response_model=PriceObservation, status_code=status.HTTP_201_CREATED)
def addManualPrice(
    submitted: ManualPriceInput,
    prices: Annotated[SqlitePriceObservationRepository, Depends(getPriceRepository)],
    venues: Annotated[VenueRepository, Depends(getVenueRepository)],
) -> PriceObservation:
    """Publish a person's exact menu observation and derive its price band."""
    venue = next(
        (item for item in venues.listVenues() if item.venueId == submitted.venueId),
        None,
    )
    if venue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pub not found")
    return prices.publish(
        PriceObservationInput(
            venueId=venue.venueId,
            venueName=venue.name,
            priceLevel=guinnessPriceLevel(submitted.observedPriceGbp),
            observedItem="Pint of draught Guinness",
            observedPriceGbp=submitted.observedPriceGbp,
            evidenceUrl=submitted.evidenceUrl,
        )
    )
