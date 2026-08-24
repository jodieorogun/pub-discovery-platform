"""Local authentication and personal rating orchestration."""

import base64
import binascii
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from uuid import uuid4

from app.models.account import UserAccount, VenueRating
from app.repositories.sqlite_account_repository import SqliteAccountRepository
from app.repositories.venue_repository import VenueRepository
from app.schemas.account import DiaryEntry, DiaryStats, LoginRequest, RatingRequest, RegisterRequest

SESSION_DAYS = 30


class AccountConflictError(ValueError):
    pass


class InvalidLoginError(ValueError):
    pass


class UnknownRatedVenueError(ValueError):
    pass


class InvalidPhotoError(ValueError):
    pass


class AccountService:
    """Use secure stdlib primitives until an external identity provider is added."""

    def __init__(
        self, repository: SqliteAccountRepository, venueRepository: VenueRepository
    ) -> None:
        self.repository = repository
        self.venues = {venue.venueId: venue for venue in venueRepository.listVenues()}
        self.knownVenueIds = set(self.venues)

    def register(self, request: RegisterRequest) -> tuple[UserAccount, str]:
        now = datetime.now(UTC)
        account = UserAccount(
            userId=str(uuid4()),
            email=str(request.email).lower(),
            displayName=request.displayName,
            createdAt=now,
        )
        if not self.repository.createUser(account, _hashPassword(request.password)):
            raise AccountConflictError("An account already exists for this email")
        return account, self._newSession(account.userId, now)

    def ensureDemoAdmin(self, email: str, password: str) -> None:
        """Create the temporary local demo account when it does not exist."""
        if self.repository.getCredentials(email) is not None:
            return
        now = datetime.now(UTC)
        account = UserAccount(
            userId=str(uuid4()),
            email=email.lower(),
            displayName="Admin",
            createdAt=now,
        )
        self.repository.createUser(account, _hashPassword(password))

    def login(self, request: LoginRequest) -> tuple[UserAccount, str]:
        credentials = self.repository.getCredentials(str(request.email))
        if credentials is None or not _verifyPassword(request.password, credentials[1]):
            raise InvalidLoginError("Email or password is incorrect")
        account = credentials[0]
        return account, self._newSession(account.userId, datetime.now(UTC))

    def authenticate(self, token: str | None) -> UserAccount | None:
        if not token:
            return None
        return self.repository.getUserForSession(_hashToken(token), datetime.now(UTC))

    def logout(self, token: str | None) -> None:
        if token:
            self.repository.deleteSession(_hashToken(token))

    def rate(self, userId: str, venueId: str, request: RatingRequest) -> VenueRating:
        if venueId not in self.knownVenueIds:
            raise UnknownRatedVenueError(f"Unknown venueId: {venueId}")
        now = datetime.now(UTC)
        existing = next(
            (item for item in self.repository.listRatings(userId) if item.venueId == venueId),
            None,
        )
        photo = _decodePhoto(request.photoDataUrl) if request.photoDataUrl else None
        rating = VenueRating(
            userId=userId,
            venueId=venueId,
            beenHere=request.beenHere,
            rating=request.rating,
            privateNote=(
                request.privateNote
                if "privateNote" in request.model_fields_set
                else (existing.privateNote if existing else None)
            ),
            hasPhoto=photo is not None or bool(existing and existing.hasPhoto),
            visitedAt=existing.visitedAt if existing else now,
            updatedAt=now,
        )
        self.repository.upsertRating(rating, photo)
        return rating

    def listRatings(self, userId: str) -> list[VenueRating]:
        return self.repository.listRatings(userId)

    def diary(self, userId: str) -> list[DiaryEntry]:
        entries = []
        for rating in self.repository.listRatings(userId):
            if not rating.beenHere:
                continue
            venue = self.venues[rating.venueId]
            entries.append(
                DiaryEntry(
                    venueId=venue.venueId,
                    name=venue.name,
                    area=venue.area,
                    address=venue.address,
                    postcode=venue.postcode,
                    rating=rating.rating,
                    privateNote=rating.privateNote,
                    photoUrl=(
                        f"/account/diary/{venue.venueId}/photo" if rating.hasPhoto else None
                    ),
                    visitedAt=rating.visitedAt,
                    updatedAt=rating.updatedAt,
                )
            )
        return entries

    def stats(self, userId: str, now: datetime | None = None) -> DiaryStats:
        current = now or datetime.now(UTC)
        weekStart = current.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=current.weekday()
        )
        ratings = [rating for rating in self.repository.listRatings(userId) if rating.beenHere]
        scores = [rating.rating for rating in ratings if rating.rating is not None]
        londonIds = set(self.venues)
        londonVisited = len({rating.venueId for rating in ratings if rating.venueId in londonIds})
        return DiaryStats(
            visitedTotal=len(ratings),
            visitedThisWeek=sum(rating.visitedAt >= weekStart for rating in ratings),
            ratedTotal=len(scores),
            averageRating=round(sum(scores) / len(scores), 1) if scores else None,
            londonVisited=londonVisited,
            londonTotal=len(londonIds),
            londonPercent=(round(100 * londonVisited / len(londonIds), 1) if londonIds else 0),
        )

    def photo(self, userId: str, venueId: str) -> tuple[str, bytes] | None:
        return self.repository.getPhoto(userId, venueId)

    def _newSession(self, userId: str, now: datetime) -> str:
        token = secrets.token_urlsafe(32)
        self.repository.createSession(
            _hashToken(token), userId, now + timedelta(days=SESSION_DAYS), now
        )
        return token


def _hashPassword(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def _verifyPassword(password: str, encoded: str) -> bool:
    try:
        algorithm, saltValue, digestValue = encoded.split("$")
        if algorithm != "scrypt":
            return False
        salt = base64.b64decode(saltValue)
        expected = base64.b64decode(digestValue)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _hashToken(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _decodePhoto(dataUrl: str) -> tuple[str, bytes]:
    """Accept small browser-generated JPEG, PNG, or WebP images only."""
    try:
        header, encoded = dataUrl.split(",", 1)
        mime = header.removeprefix("data:").removesuffix(";base64")
        if mime not in {"image/jpeg", "image/png", "image/webp"} or not header.endswith(
            ";base64"
        ):
            raise InvalidPhotoError("Photo must be a JPEG, PNG, or WebP image")
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise InvalidPhotoError("Photo data is invalid") from error
    if not content or len(content) > 5_000_000:
        raise InvalidPhotoError("Photo must be smaller than 5 MB")
    signatures = {
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/webp": (b"RIFF",),
    }
    if not any(content.startswith(signature) for signature in signatures[mime]):
        raise InvalidPhotoError("Photo contents do not match its image type")
    if mime == "image/webp" and content[8:12] != b"WEBP":
        raise InvalidPhotoError("Photo contents do not match its image type")
    return mime, content
