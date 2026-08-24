"""Fetch FSQ Places search results and normalise them for source matching."""

import argparse
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values
from pydantic import TypeAdapter, ValidationError

from app.source_matching.source_models import FsqPlaceRecord

FSQ_SEARCH_URL = "https://places-api.foursquare.com/places/search"
FSQ_API_VERSION = "2025-06-17"
FSQ_SEARCH_FIELDS = ",".join(
    (
        "fsq_place_id",
        "name",
        "latitude",
        "longitude",
        "location",
        "categories",
        "tel",
        "website",
        "price",
    )
)


class FsqImportError(RuntimeError):
    """A safe, user-facing FSQ import failure."""


class FsqPlacesClient:
    """Small FSQ Places client with injectable HTTP transport for tests."""

    def __init__(
        self,
        apiKey: str,
        *,
        timeoutSeconds: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        maxRetries: int = 3,
        retryDelaySeconds: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not apiKey.strip():
            raise FsqImportError("FSQ_API_KEY is empty")
        if timeoutSeconds <= 0:
            raise FsqImportError("FSQ timeout must be positive")
        if maxRetries < 0:
            raise FsqImportError("FSQ retry count cannot be negative")
        if retryDelaySeconds < 0:
            raise FsqImportError("FSQ retry delay cannot be negative")
        self.apiKey = apiKey
        self.timeoutSeconds = timeoutSeconds
        self.transport = transport
        self.maxRetries = maxRetries
        self.retryDelaySeconds = retryDelaySeconds
        self.sleeper = sleeper

    def search(
        self,
        near: str,
        query: str | None = None,
        limit: int = 10,
        categoryIds: str | None = None,
    ) -> list[FsqPlaceRecord]:
        """Search a named area and return normalised records."""
        if not near.strip():
            raise FsqImportError("Search area cannot be empty")
        if not (query and query.strip()) and not (categoryIds and categoryIds.strip()):
            raise FsqImportError("Provide a search query or FSQ category IDs")
        if not 1 <= limit <= 50:
            raise FsqImportError("FSQ search limit must be between 1 and 50")

        parameters: dict[str, str | int] = {
            "near": near,
            "limit": limit,
            "tel_format": "E164",
            "fields": FSQ_SEARCH_FIELDS,
        }
        if query and query.strip():
            parameters["query"] = query
        if categoryIds and categoryIds.strip():
            parameters["fsq_category_ids"] = categoryIds

        return self._request(parameters)

    def searchBounds(
        self,
        northEast: tuple[float, float],
        southWest: tuple[float, float],
        categoryIds: str,
        limit: int = 50,
    ) -> list[FsqPlaceRecord]:
        """Search a bounded map tile using an explicit FSQ category."""
        if not categoryIds.strip():
            raise FsqImportError("FSQ category IDs cannot be empty")
        if not 1 <= limit <= 50:
            raise FsqImportError("FSQ search limit must be between 1 and 50")
        parameters: dict[str, str | int] = {
            "ne": f"{northEast[0]},{northEast[1]}",
            "sw": f"{southWest[0]},{southWest[1]}",
            "fsq_category_ids": categoryIds,
            "limit": limit,
            "tel_format": "E164",
            "fields": FSQ_SEARCH_FIELDS,
        }
        return self._request(parameters)

    def _request(self, parameters: dict[str, str | int]) -> list[FsqPlaceRecord]:
        """Execute one authenticated Places search request."""

        try:
            with httpx.Client(timeout=self.timeoutSeconds, transport=self.transport) as client:
                response = None
                for attempt in range(self.maxRetries + 1):
                    # FSQ can throttle tiled imports. Retry only HTTP 429 and
                    # keep the retry count bounded so a spent quota fails fast.
                    response = client.get(
                        FSQ_SEARCH_URL,
                        headers={
                            "Authorization": f"Bearer {self.apiKey}",
                            "X-Places-Api-Version": FSQ_API_VERSION,
                        },
                        params=parameters,
                    )
                    if response.status_code != 429 or attempt == self.maxRetries:
                        break
                    delay = self._retryDelay(response, attempt)
                    self.sleeper(min(delay, 30.0))
                if response is None:
                    raise FsqImportError("FSQ request did not produce a response")
                response.raise_for_status()
        except httpx.HTTPStatusError as error:
            raise FsqImportError(
                f"FSQ returned HTTP {error.response.status_code}; check the key and search"
            ) from error
        except httpx.RequestError as error:
            raise FsqImportError(f"Could not reach FSQ: {error}") from error

        return normaliseFsqPayload(response.json())

    def _retryDelay(self, response: httpx.Response, attempt: int) -> float:
        """Use a valid Retry-After value, otherwise fall back to bounded backoff."""
        retryAfter = response.headers.get("Retry-After")
        if retryAfter:
            try:
                return max(float(retryAfter), 0.0)
            except ValueError:
                # Some gateways return a date or malformed value instead of seconds.
                pass
        return self.retryDelaySeconds * (attempt + 1)


def normaliseFsqPayload(payload: Any) -> list[FsqPlaceRecord]:
    """Convert an FSQ response into the matcher's stable source schema."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise FsqImportError("FSQ response did not contain a results list")

    records: list[FsqPlaceRecord] = []
    try:
        for rawPlace in payload["results"]:
            if not isinstance(rawPlace, dict):
                raise FsqImportError("FSQ returned a non-object place record")
            location = rawPlace.get("location")
            location = location if isinstance(location, dict) else {}
            categories = rawPlace.get("categories")
            categories = categories if isinstance(categories, list) else []
            records.append(
                FsqPlaceRecord(
                    fsqPlaceId=rawPlace.get("fsq_place_id"),
                    name=rawPlace.get("name"),
                    latitude=rawPlace.get("latitude"),
                    longitude=rawPlace.get("longitude"),
                    address=location.get("address") or location.get("formatted_address"),
                    postcode=location.get("postcode"),
                    website=rawPlace.get("website"),
                    phone=rawPlace.get("tel"),
                    priceTier=rawPlace.get("price"),
                    categories=[
                        category["name"]
                        for category in categories
                        if isinstance(category, dict) and isinstance(category.get("name"), str)
                    ],
                )
            )
    except ValidationError as error:
        raise FsqImportError(f"FSQ returned an invalid place record: {error}") from error
    return records


def loadFsqApiKey(envPath: Path) -> str:
    """Read the key from the process environment or a local dotenv file."""
    value = os.environ.get("FSQ_API_KEY")
    if value:
        return value
    dotenvValue = dotenv_values(envPath).get("FSQ_API_KEY")
    if not dotenvValue:
        raise FsqImportError(f"FSQ_API_KEY is missing from {envPath}")
    return dotenvValue


def writeFsqPlaces(places: list[FsqPlaceRecord], outputPath: Path) -> None:
    """Write normalised source records without publishing them as canonical venues."""
    outputPath.parent.mkdir(parents=True, exist_ok=True)
    outputPath.write_text(
        TypeAdapter(list[FsqPlaceRecord]).dump_json(places, indent=2).decode() + "\n",
        encoding="utf-8",
    )


def main() -> int:
    """Fetch and write an FSQ source file from the command line."""
    parser = argparse.ArgumentParser(description="Import FSQ Places search results")
    parser.add_argument("--near", required=True, help="Named locality or neighbourhood")
    parser.add_argument("--query", help="Optional place search term")
    parser.add_argument(
        "--category-ids",
        help="Comma-separated FSQ category IDs (recommended for clean imports)",
    )
    parser.add_argument("--limit", type=int, default=10, choices=range(1, 51))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    arguments = parser.parse_args()

    try:
        places = FsqPlacesClient(loadFsqApiKey(arguments.env_file)).search(
            arguments.near,
            arguments.query,
            arguments.limit,
            arguments.category_ids,
        )
        writeFsqPlaces(places, arguments.output)
    except FsqImportError as error:
        parser.error(str(error))
    print(f"Imported {len(places)} FSQ places to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
