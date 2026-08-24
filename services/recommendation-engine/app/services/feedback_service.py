"""Recommendation feedback orchestration."""

from datetime import UTC, datetime
from uuid import uuid4

from app.models.feedback import FeedbackEvent
from app.repositories.feedback_repository import FeedbackRepository
from app.repositories.venue_repository import VenueRepository
from app.schemas.feedback import FeedbackRequest


class UnknownVenueError(ValueError):
    """Raised when feedback references a venue outside the active dataset."""


class FeedbackService:
    """Validate venue references and record anonymous feedback."""

    def __init__(
        self,
        feedbackRepository: FeedbackRepository,
        venueRepository: VenueRepository,
    ) -> None:
        self.feedbackRepository = feedbackRepository
        self.knownVenueIds = {venue.venueId for venue in venueRepository.listVenues()}

    def record(self, request: FeedbackRequest) -> FeedbackEvent:
        """Create and persist one immutable feedback event."""
        if request.venueId not in self.knownVenueIds:
            raise UnknownVenueError(f"Unknown venueId: {request.venueId}")
        event = FeedbackEvent(
            eventId=str(uuid4()),
            venueId=request.venueId,
            eventType=request.eventType,
            query=request.query,
            recommendationRank=request.recommendationRank,
            sessionId=request.sessionId,
            requestId=request.requestId,
            rankingVersion=request.rankingVersion,
            datasetVersion=request.datasetVersion,
            recordedAt=datetime.now(UTC),
        )
        self.feedbackRepository.record(event)
        return event
