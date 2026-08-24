"""FSQ tiled import tests."""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.source_import import fsq_grid_importer
from app.source_import.fsq_grid_importer import (
    BoundingBox,
    buildGrid,
    importFsqGrid,
    parseBoundingBox,
)
from app.source_import.fsq_importer import FsqImportError
from app.source_matching.source_models import FsqPlaceRecord


def place(placeId: str, name: str) -> FsqPlaceRecord:
    """Build a minimal normalised place."""
    return FsqPlaceRecord(
        fsqPlaceId=placeId,
        name=name,
        latitude=51.5,
        longitude=-0.13,
    )


class FakeBoundsClient:
    """Return prepared results for successive grid tiles."""

    def __init__(self, responses: list[list[FsqPlaceRecord]]) -> None:
        self.responses: Iterator[list[FsqPlaceRecord]] = iter(responses)
        self.calls = 0

    def searchBounds(
        self,
        northEast: tuple[float, float],
        southWest: tuple[float, float],
        categoryIds: str,
        limit: int = 50,
    ) -> list[FsqPlaceRecord]:
        self.calls += 1
        assert northEast[0] > southWest[0]
        assert categoryIds == "pub-category"
        assert limit == 50
        return next(self.responses)


def testBuildGridCoversBounds() -> None:
    bounds = parseBoundingBox("51.0,-0.2,52.0,0.2")
    grid = buildGrid(bounds, 2, 2)

    assert len(grid) == 4
    assert grid[0] == ((51.0, -0.2), (51.5, 0.0))
    assert grid[-1] == ((51.5, 0.0), (52.0, 0.2))


def testGridImportDeduplicatesAndSorts() -> None:
    duplicate = place("fsq-1", "Zebra Pub")
    client = FakeBoundsClient(
        [[duplicate], [place("fsq-2", "Alpha Arms")], [duplicate], []]
    )

    places = importFsqGrid(
        client,
        BoundingBox(51.0, -0.2, 52.0, 0.2),
        "pub-category",
        2,
        2,
    )

    assert client.calls == 4
    assert [result.fsqPlaceId for result in places] == ["fsq-2", "fsq-1"]


def testGridImportPacesRequestsBetweenTiles() -> None:
    delays: list[float] = []
    client = FakeBoundsClient([[], []])

    importFsqGrid(
        client,
        BoundingBox(51.0, -0.2, 52.0, 0.2),
        "pub-category",
        1,
        2,
        delaySeconds=0.5,
        sleeper=delays.append,
    )

    assert delays == [0.5]


def testGridImportRejectsNegativeDelay() -> None:
    with pytest.raises(FsqImportError, match="delay"):
        importFsqGrid(
            FakeBoundsClient([]),
            BoundingBox(51.0, -0.2, 52.0, 0.2),
            "pub-category",
            1,
            1,
            delaySeconds=-0.1,
        )


@pytest.mark.parametrize(
    "value",
    ["wrong", "1,2,3", "south,west,north,east"],
)
def testRejectsMalformedBoundingBox(value: str) -> None:
    with pytest.raises(FsqImportError, match="four numbers"):
        parseBoundingBox(value)


@pytest.mark.parametrize(
    "bounds",
    [
        (52.0, -0.2, 51.0, 0.2),
        (51.0, 0.2, 52.0, -0.2),
        (-91.0, -0.2, 52.0, 0.2),
    ],
)
def testRejectsInvalidBoundingBox(bounds: tuple[float, float, float, float]) -> None:
    with pytest.raises(FsqImportError, match="Bounding box"):
        BoundingBox(*bounds)


def testRejectsInvalidGridSize() -> None:
    with pytest.raises(FsqImportError, match="positive"):
        buildGrid(BoundingBox(51.0, -0.2, 52.0, 0.2), 0, 2)


def testMainWritesGridImport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outputPath = tmp_path / "grid.json"
    client = FakeBoundsClient([[place("fsq-1", "Example Pub")]])
    monkeypatch.setenv("FSQ_API_KEY", "secret")
    monkeypatch.setattr(fsq_grid_importer, "FsqPlacesClient", lambda apiKey: client)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fsq_grid_importer",
            "--bbox",
            "51.0,-0.2,52.0,0.2",
            "--category-ids",
            "pub-category",
            "--rows",
            "1",
            "--columns",
            "1",
            "--output",
            str(outputPath),
        ],
    )

    assert fsq_grid_importer.main() == 0
    assert outputPath.exists()
    assert "Imported 1 unique FSQ places from 1 tiles" in capsys.readouterr().out
