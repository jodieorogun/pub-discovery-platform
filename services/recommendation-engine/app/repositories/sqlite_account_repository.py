"""SQLite storage for local accounts, sessions, and personal pub ratings."""

import sqlite3
from datetime import datetime
from pathlib import Path

from app.models.account import UserAccount, VenueRating


class SqliteAccountRepository:
    """Keep the first account implementation local and dependency-light."""

    def __init__(self, databasePath: Path) -> None:
        self.databasePath = databasePath
        self.databasePath.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.databasePath) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS user_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS venue_ratings (
                    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    venue_id TEXT NOT NULL,
                    been_here INTEGER NOT NULL DEFAULT 1,
                    rating INTEGER CHECK (rating BETWEEN 1 AND 5),
                    private_note TEXT,
                    photo_mime TEXT,
                    photo_blob BLOB,
                    visited_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, venue_id)
                );
                CREATE INDEX IF NOT EXISTS idx_venue_ratings_user
                    ON venue_ratings(user_id);
                """
            )
            # Upgrade existing local account databases without a migration dependency.
            existingColumns = {
                row[1] for row in connection.execute("PRAGMA table_info(venue_ratings)")
            }
            for definition in (
                "private_note TEXT",
                "photo_mime TEXT",
                "photo_blob BLOB",
                "visited_at TEXT",
            ):
                if definition.split()[0] not in existingColumns:
                    connection.execute(f"ALTER TABLE venue_ratings ADD COLUMN {definition}")

    def createUser(self, account: UserAccount, passwordHash: str) -> bool:
        try:
            with sqlite3.connect(self.databasePath) as connection:
                connection.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                    (
                        account.userId,
                        account.email.lower(),
                        account.displayName,
                        passwordHash,
                        account.createdAt.isoformat(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def getCredentials(self, email: str) -> tuple[UserAccount, str] | None:
        with sqlite3.connect(self.databasePath) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
            ).fetchone()
        if row is None:
            return None
        return self._account(row), str(row["password_hash"])

    def updatePasswordHash(self, email: str, passwordHash: str) -> None:
        """Update a local demo credential by email."""
        with sqlite3.connect(self.databasePath) as connection:
            connection.execute(
                "UPDATE users SET password_hash = ? WHERE email = ? COLLATE NOCASE",
                (passwordHash, email),
            )

    def createSession(
        self, tokenHash: str, userId: str, expiresAt: datetime, createdAt: datetime
    ) -> None:
        with sqlite3.connect(self.databasePath) as connection:
            connection.execute(
                "INSERT INTO user_sessions VALUES (?, ?, ?, ?)",
                (tokenHash, userId, expiresAt.isoformat(), createdAt.isoformat()),
            )

    def getUserForSession(self, tokenHash: str, now: datetime) -> UserAccount | None:
        with sqlite3.connect(self.databasePath) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT users.* FROM user_sessions
                JOIN users USING (user_id)
                WHERE token_hash = ? AND expires_at > ?
                """,
                (tokenHash, now.isoformat()),
            ).fetchone()
        return self._account(row) if row is not None else None

    def deleteSession(self, tokenHash: str) -> None:
        with sqlite3.connect(self.databasePath) as connection:
            connection.execute("DELETE FROM user_sessions WHERE token_hash = ?", (tokenHash,))

    def upsertRating(
        self, rating: VenueRating, photo: tuple[str, bytes] | None = None
    ) -> None:
        with sqlite3.connect(self.databasePath) as connection:
            connection.execute(
                """
                INSERT INTO venue_ratings (
                    user_id, venue_id, been_here, rating, private_note,
                    photo_mime, photo_blob, visited_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, venue_id) DO UPDATE SET
                    been_here = excluded.been_here,
                    rating = excluded.rating,
                    private_note = excluded.private_note,
                    photo_mime = COALESCE(excluded.photo_mime, venue_ratings.photo_mime),
                    photo_blob = COALESCE(excluded.photo_blob, venue_ratings.photo_blob),
                    visited_at = COALESCE(venue_ratings.visited_at, excluded.visited_at),
                    updated_at = excluded.updated_at
                """,
                (
                    rating.userId,
                    rating.venueId,
                    int(rating.beenHere),
                    rating.rating,
                    rating.privateNote,
                    photo[0] if photo else None,
                    photo[1] if photo else None,
                    rating.visitedAt.isoformat(),
                    rating.updatedAt.isoformat(),
                ),
            )

    def listRatings(self, userId: str) -> list[VenueRating]:
        with sqlite3.connect(self.databasePath) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT * FROM venue_ratings WHERE user_id = ? ORDER BY updated_at DESC",
                (userId,),
            ).fetchall()
        return [
            VenueRating(
                userId=row["user_id"],
                venueId=row["venue_id"],
                beenHere=bool(row["been_here"]),
                rating=row["rating"],
                privateNote=row["private_note"],
                hasPhoto=row["photo_blob"] is not None,
                visitedAt=datetime.fromisoformat(row["visited_at"] or row["updated_at"]),
                updatedAt=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]

    def getPhoto(self, userId: str, venueId: str) -> tuple[str, bytes] | None:
        """Return a private photo only after its owner has been authenticated."""
        with sqlite3.connect(self.databasePath) as connection:
            row = connection.execute(
                """
                SELECT photo_mime, photo_blob FROM venue_ratings
                WHERE user_id = ? AND venue_id = ?
                """,
                (userId, venueId),
            ).fetchone()
        if row is None or row[0] is None or row[1] is None:
            return None
        return str(row[0]), bytes(row[1])

    @staticmethod
    def _account(row: sqlite3.Row) -> UserAccount:
        return UserAccount(
            userId=row["user_id"],
            email=row["email"],
            displayName=row["display_name"],
            createdAt=datetime.fromisoformat(row["created_at"]),
        )
