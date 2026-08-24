"""Filter FSQ source records against an exact GeoJSON boundary."""

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from app.source_import.fsq_importer import writeFsqPlaces
from app.source_matching.source_models import FsqPlaceRecord


class BoundaryFilterError(RuntimeError):
    """A malformed or unsupported boundary input."""


def filterFsqPlaces(
    places: list[FsqPlaceRecord],
    geoJson: Any,
) -> list[FsqPlaceRecord]:
    """Retain only place coordinates inside the supplied boundary."""
    geometry = extractGeometry(geoJson)
    return [
        place
        for place in places
        if isPointInGeometry(place.longitude, place.latitude, geometry)
    ]


def extractGeometry(geoJson: Any) -> dict[str, Any]:
    """Extract a Polygon or MultiPolygon from common GeoJSON containers."""
    if not isinstance(geoJson, dict):
        raise BoundaryFilterError("Boundary GeoJSON must be an object")
    geoJsonType = geoJson.get("type")
    if geoJsonType == "FeatureCollection":
        features = geoJson.get("features")
        if not isinstance(features, list) or not features:
            raise BoundaryFilterError("Boundary FeatureCollection is empty")
        return extractGeometry(features[0])
    if geoJsonType == "Feature":
        return extractGeometry(geoJson.get("geometry"))
    if geoJsonType not in {"Polygon", "MultiPolygon"}:
        raise BoundaryFilterError("Boundary must contain a Polygon or MultiPolygon")
    if not isinstance(geoJson.get("coordinates"), list):
        raise BoundaryFilterError("Boundary coordinates are missing")
    return geoJson


def isPointInGeometry(longitude: float, latitude: float, geometry: dict[str, Any]) -> bool:
    """Test a WGS84 point against Polygon or MultiPolygon coordinates."""
    coordinates = geometry["coordinates"]
    polygons = coordinates if geometry["type"] == "MultiPolygon" else [coordinates]
    return any(isPointInPolygon(longitude, latitude, polygon) for polygon in polygons)


def isPointInPolygon(longitude: float, latitude: float, polygon: Any) -> bool:
    """Include the outer ring and exclude any interior holes."""
    if not isinstance(polygon, list) or not polygon:
        raise BoundaryFilterError("Boundary polygon has no rings")
    return pointInRing(longitude, latitude, polygon[0]) and not any(
        pointInRing(longitude, latitude, hole) for hole in polygon[1:]
    )


def pointInRing(longitude: float, latitude: float, ring: Any) -> bool:
    """Use ray casting, treating points on the boundary as inside."""
    if not isinstance(ring, list) or len(ring) < 4:
        raise BoundaryFilterError("Boundary ring requires at least four coordinates")
    points = [coordinatePair(value) for value in ring]
    inside = False
    previousLongitude, previousLatitude = points[-1]
    for currentLongitude, currentLatitude in points:
        if pointOnSegment(
            longitude,
            latitude,
            previousLongitude,
            previousLatitude,
            currentLongitude,
            currentLatitude,
        ):
            return True
        crossesLatitude = (currentLatitude > latitude) != (previousLatitude > latitude)
        if crossesLatitude:
            crossingLongitude = (previousLongitude - currentLongitude) * (
                latitude - currentLatitude
            ) / (previousLatitude - currentLatitude) + currentLongitude
            if longitude < crossingLongitude:
                inside = not inside
        previousLongitude, previousLatitude = currentLongitude, currentLatitude
    return inside


def coordinatePair(value: Any) -> tuple[float, float]:
    """Validate one GeoJSON longitude/latitude pair."""
    if (
        not isinstance(value, list)
        or len(value) < 2
        or not isinstance(value[0], int | float)
        or not isinstance(value[1], int | float)
    ):
        raise BoundaryFilterError("Boundary contains an invalid coordinate")
    return float(value[0]), float(value[1])


def pointOnSegment(
    pointLongitude: float,
    pointLatitude: float,
    startLongitude: float,
    startLatitude: float,
    endLongitude: float,
    endLatitude: float,
) -> bool:
    """Return whether a point lies on a line segment within floating tolerance."""
    crossProduct = (pointLatitude - startLatitude) * (endLongitude - startLongitude) - (
        pointLongitude - startLongitude
    ) * (endLatitude - startLatitude)
    if abs(crossProduct) > 1e-12:
        return False
    return (
        min(startLongitude, endLongitude) <= pointLongitude <= max(startLongitude, endLongitude)
        and min(startLatitude, endLatitude) <= pointLatitude <= max(startLatitude, endLatitude)
    )


def main() -> int:
    """Filter an FSQ JSON file using a cached GeoJSON boundary."""
    parser = argparse.ArgumentParser(description="Filter FSQ places by GeoJSON boundary")
    parser.add_argument("inputPath", type=Path)
    parser.add_argument("boundaryPath", type=Path)
    parser.add_argument("outputPath", type=Path)
    arguments = parser.parse_args()

    places = TypeAdapter(list[FsqPlaceRecord]).validate_json(
        arguments.inputPath.read_text(encoding="utf-8")
    )
    boundary = json.loads(arguments.boundaryPath.read_text(encoding="utf-8"))
    filteredPlaces = filterFsqPlaces(places, boundary)
    writeFsqPlaces(filteredPlaces, arguments.outputPath)
    print(
        f"Retained {len(filteredPlaces)} of {len(places)} FSQ places inside the boundary"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
