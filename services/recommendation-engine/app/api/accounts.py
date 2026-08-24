"""Small local account and personal-rating API."""

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import Response as BinaryResponse

from app.models.account import UserAccount, VenueRating
from app.schemas.account import (
    DiaryEntry,
    DiaryStats,
    LoginRequest,
    RatingRequest,
    RegisterRequest,
)
from app.services.account_service import (
    AccountConflictError,
    AccountService,
    InvalidLoginError,
    InvalidPhotoError,
    UnknownRatedVenueError,
)

router = APIRouter(prefix="/account", tags=["account"])
SESSION_COOKIE = "pub_session"


def getAccountService(request: Request) -> AccountService:
    return request.app.state.accountService  # type: ignore[no-any-return]


def _setSession(response: Response, token: str) -> None:
    # HttpOnly keeps the credential away from client-side JavaScript.
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        samesite="lax",
    )


def requireAccount(
    accountService: Annotated[AccountService, Depends(getAccountService)],
    pub_session: Annotated[str | None, Cookie()] = None,
) -> UserAccount:
    account = accountService.authenticate(pub_session)
    if account is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required")
    return account


@router.post("/register", response_model=UserAccount, status_code=status.HTTP_201_CREATED)
def register(
    requestBody: RegisterRequest,
    response: Response,
    accountService: Annotated[AccountService, Depends(getAccountService)],
) -> UserAccount:
    try:
        account, token = accountService.register(requestBody)
    except AccountConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    _setSession(response, token)
    return account


@router.post("/login", response_model=UserAccount)
def login(
    requestBody: LoginRequest,
    response: Response,
    accountService: Annotated[AccountService, Depends(getAccountService)],
) -> UserAccount:
    try:
        account, token = accountService.login(requestBody)
    except InvalidLoginError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    _setSession(response, token)
    return account


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    accountService: Annotated[AccountService, Depends(getAccountService)],
    pub_session: Annotated[str | None, Cookie()] = None,
) -> None:
    accountService.logout(pub_session)
    response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="lax")


@router.get("/me", response_model=UserAccount)
def getMe(account: Annotated[UserAccount, Depends(requireAccount)]) -> UserAccount:
    return account


@router.get("/ratings", response_model=list[VenueRating])
def listRatings(
    account: Annotated[UserAccount, Depends(requireAccount)],
    accountService: Annotated[AccountService, Depends(getAccountService)],
) -> list[VenueRating]:
    return accountService.listRatings(account.userId)


@router.get("/diary", response_model=list[DiaryEntry])
def getDiary(
    account: Annotated[UserAccount, Depends(requireAccount)],
    accountService: Annotated[AccountService, Depends(getAccountService)],
) -> list[DiaryEntry]:
    return accountService.diary(account.userId)


@router.get("/stats", response_model=DiaryStats)
def getStats(
    account: Annotated[UserAccount, Depends(requireAccount)],
    accountService: Annotated[AccountService, Depends(getAccountService)],
) -> DiaryStats:
    return accountService.stats(account.userId)


@router.get("/diary/{venueId}/photo", response_class=BinaryResponse)
def getDiaryPhoto(
    venueId: str,
    account: Annotated[UserAccount, Depends(requireAccount)],
    accountService: Annotated[AccountService, Depends(getAccountService)],
) -> BinaryResponse:
    photo = accountService.photo(account.userId, venueId)
    if photo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    return BinaryResponse(
        photo[1],
        media_type=photo[0],
        headers={"Cache-Control": "private", "X-Content-Type-Options": "nosniff"},
    )


@router.put("/ratings/{venueId}", response_model=VenueRating)
def rateVenue(
    venueId: str,
    requestBody: RatingRequest,
    account: Annotated[UserAccount, Depends(requireAccount)],
    accountService: Annotated[AccountService, Depends(getAccountService)],
) -> VenueRating:
    try:
        return accountService.rate(account.userId, venueId, requestBody)
    except UnknownRatedVenueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except InvalidPhotoError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
