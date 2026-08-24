"""Import an FSQ category across a grid of bounded Places searches."""

import argparse
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.source_import.fsq_importer import (
    FsqImportError,
    FsqPlacesClient,
    loadFsqApiKey,
    writeFsqPlaces,
)
from app.source_matching.source_models import FsqPlaceRecord


@dataclass(frozen=True)
class BoundingBox:
    """South-west and north-east limits for a grid import."""

    south: float
    west: float
    north: float
    east: float

    def __post_init__(self) -> None:
        if not -90 <= self.south < self.north <= 90:
            raise FsqImportError("Bounding box requires south < north within latitude limits")
        if not -180 <= self.west < self.east <= 180:
            raise FsqImportError("Bounding box requires west < east within longitude limits")


class BoundsSearchClient(Protocol):
    """Required client operation for tiled imports."""

    def searchBounds(
        self,
        northEast: tuple[float, float],
        southWest: tuple[float, float],
        categoryIds: str,
        limit: int = 50,
    ) -> list[FsqPlaceRecord]: ...


def parseBoundingBox(value: str) -> BoundingBox:
    """Parse south,west,north,east coordinates from the CLI."""
    try:
        south, west, north, east = (float(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise FsqImportError(
            "Bounding box must be four numbers: south,west,north,east"
        ) from error
    return BoundingBox(south=south, west=west, north=north, east=east)


def buildGrid(
    bounds: BoundingBox,
    rows: int,
    columns: int,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Split bounds into deterministic south-west/north-east tiles."""
    if rows < 1 or columns < 1:
        raise FsqImportError("Grid rows and columns must be positive")
    latitudeStep = (bounds.north - bounds.south) / rows
    longitudeStep = (bounds.east - bounds.west) / columns
    return [
        (
            (
                bounds.south + row * latitudeStep,
                bounds.west + column * longitudeStep,
            ),
            (
                bounds.south + (row + 1) * latitudeStep,
                bounds.west + (column + 1) * longitudeStep,
            ),
        )
        for row in range(rows)
        for column in range(columns)
    ]


def importFsqGrid(
    client: BoundsSearchClient,
    bounds: BoundingBox,
    categoryIds: str,
    rows: int,
    columns: int,
    delaySeconds: float = 0.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[FsqPlaceRecord]:
    """Search every tile and deduplicate overlapping FSQ place IDs."""
    if delaySeconds < 0:
        raise FsqImportError("Grid request delay cannot be negative")
    placesById: dict[str, FsqPlaceRecord] = {}
    tiles = buildGrid(bounds, rows, columns)
    for tileIndex, (southWest, northEast) in enumerate(tiles):
        for place in client.searchBounds(northEast, southWest, categoryIds):
            placesById[place.fsqPlaceId] = place
        if delaySeconds > 0 and tileIndex < len(tiles) - 1:
            # Pacing prevents free-tier keys from being throttled mid-grid.
            sleeper(delaySeconds)
    return sorted(placesById.values(), key=lambda place: (place.name.casefold(), place.fsqPlaceId))


def main() -> int:
    """Import and deduplicate category results across map tiles."""
    parser = argparse.ArgumentParser(description="Import FSQ Places across a map grid")
    parser.add_argument("--bbox", required=True, help="south,west,north,east")
    parser.add_argument("--category-ids", required=True)
    parser.add_argument("--rows", type=int, default=3)
    parser.add_argument("--columns", type=int, default=3)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    arguments = parser.parse_args()

    try:
        places = importFsqGrid(
            FsqPlacesClient(loadFsqApiKey(arguments.env_file)),
            parseBoundingBox(arguments.bbox),
            arguments.category_ids,
            arguments.rows,
            arguments.columns,
            arguments.delay_seconds,
        )
        writeFsqPlaces(places, arguments.output)
    except FsqImportError as error:
        parser.error(str(error))
    print(
        f"Imported {len(places)} unique FSQ places from "
        f"{arguments.rows * arguments.columns} tiles to {arguments.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
