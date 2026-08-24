"""Exact GeoJSON boundary filter tests."""

import json
import sys
from pathlib import Path

import pytest

from app.source_matching import boundary_filter
from app.source_matching.boundary_filter import (
    BoundaryFilterError,
    filterFsqPlaces,
    isPointInGeometry,
)
from app.source_matching.source_models import FsqPlaceRecord


def squareBoundary() -> dict[str, object]:
    """Build a square polygon with a small interior hole."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
                        [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
                    ],
                },
            }
        ],
    }


def testPointInPolygonAndHole() -> None:
    geometry = boundary_filter.extractGeometry(squareBoundary())
    assert isPointInGeometry(2, 2, geometry)
    assert isPointInGeometry(0, 5, geometry)
    assert not isPointInGeometry(5, 5, geometry)
    assert not isPointInGeometry(12, 5, geometry)


def testMultiPolygon() -> None:
    geometry = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            [[[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]],
        ],
    }
    assert isPointInGeometry(2.5, 2.5, geometry)


def testFilterFsqPlaces() -> None:
    places = [
        FsqPlaceRecord(fsqPlaceId="inside", name="Inside", latitude=2, longitude=2),
        FsqPlaceRecord(fsqPlaceId="outside", name="Outside", latitude=20, longitude=20),
    ]
    assert [place.fsqPlaceId for place in filterFsqPlaces(places, squareBoundary())] == [
        "inside"
    ]


@pytest.mark.parametrize(
    "geoJson",
    [None, {}, {"type": "FeatureCollection", "features": []}],
)
def testRejectsMalformedGeoJson(geoJson: object) -> None:
    with pytest.raises(BoundaryFilterError):
        boundary_filter.extractGeometry(geoJson)


def testMainFiltersFile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inputPath = tmp_path / "input.json"
    boundaryPath = tmp_path / "boundary.geojson"
    outputPath = tmp_path / "output.json"
    inputPath.write_text(
        '[{"fsqPlaceId":"inside","name":"Inside","latitude":2,"longitude":2}]',
        encoding="utf-8",
    )
    boundaryPath.write_text(json.dumps(squareBoundary()), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["boundary_filter", str(inputPath), str(boundaryPath), str(outputPath)],
    )

    assert boundary_filter.main() == 0
    assert json.loads(outputPath.read_text(encoding="utf-8"))[0]["fsqPlaceId"] == "inside"
    assert "Retained 1 of 1" in capsys.readouterr().out
