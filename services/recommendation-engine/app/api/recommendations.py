"""Recommendation API routes and dependencies."""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Request

from app.api.accounts import SESSION_COOKIE, getAccountService
from app.schemas.recommendation import RecommendationRequest, RecommendationResponse
from app.services.account_service import AccountService
from app.services.recommendation_service import RecommendationService

router = APIRouter(tags=["recommendations"])


def getRecommendationService(request: Request) -> RecommendationService:
    """Resolve the application-scoped recommendation service."""
    return request.app.state.recommendationService  # type: ignore[no-any-return]


@router.post("/recommendations", response_model=RecommendationResponse)
def createRecommendations(
    requestBody: RecommendationRequest,
    recommendationService: Annotated[RecommendationService, Depends(getRecommendationService)],
    accountService: Annotated[AccountService, Depends(getAccountService)],
    pub_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> RecommendationResponse:
    """Parse a query and return ranked, explainable venue recommendations."""
    account = accountService.authenticate(pub_session)
    ratings = accountService.listRatings(account.userId) if account is not None else []
    return recommendationService.recommend(requestBody, ratings)
