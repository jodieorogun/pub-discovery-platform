"""Recommendation feedback API route."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.models.feedback import FeedbackEvent
from app.schemas.feedback import FeedbackRequest
from app.services.feedback_service import FeedbackService, UnknownVenueError

router = APIRouter(tags=["feedback"])


def getFeedbackService(request: Request) -> FeedbackService:
    """Resolve the application-scoped feedback service."""
    return request.app.state.feedbackService  # type: ignore[no-any-return]


@router.post("/feedback", response_model=FeedbackEvent, status_code=status.HTTP_201_CREATED)
def createFeedback(
    requestBody: FeedbackRequest,
    feedbackService: Annotated[FeedbackService, Depends(getFeedbackService)],
) -> FeedbackEvent:
    """Record an anonymous interaction with a known recommended venue."""
    try:
        return feedbackService.record(requestBody)
    except UnknownVenueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
