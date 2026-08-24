"""Local endpoints for approving or rejecting extracted official-site facts."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.repositories.sqlite_feature_review_repository import (
    FeatureReviewDecision,
    FeatureReviewItem,
    SqliteFeatureReviewRepository,
)

router = APIRouter(prefix="/review/features", tags=["feature-review"])


def getReviewRepository(request: Request) -> SqliteFeatureReviewRepository:
    return request.app.state.featureReviewRepository  # type: ignore[no-any-return]


@router.get("", response_model=list[FeatureReviewItem])
def listFeatureReviews(
    repository: Annotated[SqliteFeatureReviewRepository, Depends(getReviewRepository)],
    reviewStatus: str = Query(
        default="manual_review", pattern="^(manual_review|approved|rejected)$"
    ),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[FeatureReviewItem]:
    """List evidence claims without applying them to canonical venue facts."""
    return repository.listItems(reviewStatus, limit)


@router.patch("/{reviewId}", response_model=FeatureReviewItem)
def decideFeatureReview(
    reviewId: int,
    decision: FeatureReviewDecision,
    repository: Annotated[SqliteFeatureReviewRepository, Depends(getReviewRepository)],
) -> FeatureReviewItem:
    """Record one explicit approve/reject decision."""
    try:
        return repository.decide(reviewId, decision)
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review item not found") from error
