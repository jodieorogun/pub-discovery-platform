"""Tests for safe multi-borough dataset combination."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.data_pipeline import dataset_combiner
from app.data_pipeline.dataset_combiner import combinePassageReports, combineVenues
from app.models.venue import Venue
from app.source_enrichment.website_passage_ingester import (
    WebsitePassage,
    WebsitePassageReport,
)


def _venue(venueId: str, area: str) -> Venue:
    return Venue(
        venueId=venueId,
        name=f"Venue {venueId}",
        latitude=51.5,
        longitude=-0.1,
        area=area,
        tags=["pub"],
    )


def testCombinesAndSortsBoroughVenues(tmp_path: Path) -> None:
    westminsterPath = tmp_path / "westminster.json"
    camdenPath = tmp_path / "camden.json"
    westminsterPath.write_text(
        "[" + _venue("westminster-1", "Westminster").model_dump_json() + "]",
        encoding="utf-8",
    )
    camdenPath.write_text(
        "[" + _venue("camden-1", "Camden").model_dump_json() + "]",
        encoding="utf-8",
    )

    venues = combineVenues([westminsterPath, camdenPath])

    assert [venue.area for venue in venues] == ["Camden", "Westminster"]


def testRejectsDuplicateVenueIds(tmp_path: Path) -> None:
    firstPath = tmp_path / "first.json"
    secondPath = tmp_path / "second.json"
    value = "[" + _venue("duplicate", "Camden").model_dump_json() + "]"
    firstPath.write_text(value, encoding="utf-8")
    secondPath.write_text(value, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        combineVenues([firstPath, secondPath])


def testCombinesPassageReportsAndDeduplicatesText(tmp_path: Path) -> None:
    passage = WebsitePassage(
        venueId="venue-1",
        venueName="Venue",
        pageUrl="https://example.com",
        text="A sufficiently long grounded passage about this example pub and its food.",
        retrievedAt=datetime(2026, 8, 10, tzinfo=UTC),
    )
    report = WebsitePassageReport(
        scannedAt=datetime(2026, 8, 10, tzinfo=UTC),
        venuesRequested=1,
        venuesScanned=1,
        pagesFetched=1,
        passages=[passage],
        failures=[],
    )
    paths = [tmp_path / "one.json", tmp_path / "two.json"]
    for path in paths:
        path.write_text(report.model_dump_json(), encoding="utf-8")

    combined = combinePassageReports(paths)

    assert combined.venuesRequested == 2
    assert combined.pagesFetched == 2
    assert len(combined.passages) == 1


def testPassageCombinationIsDeterministic(tmp_path: Path) -> None:
    passages = [
        WebsitePassage(
            venueId=venueId,
            venueName="Venue",
            pageUrl=f"https://example.com/{venueId}",
            text=f"A sufficiently long grounded passage for venue {venueId} and its menu.",
            retrievedAt=datetime(2026, 8, 10, tzinfo=UTC),
        )
        for venueId in ("venue-b", "venue-a")
    ]
    report = WebsitePassageReport(
        scannedAt=datetime(2026, 8, 10, tzinfo=UTC),
        venuesRequested=2,
        venuesScanned=2,
        pagesFetched=2,
        passages=passages,
        failures=[],
    )
    path = tmp_path / "passages.json"
    path.write_text(report.model_dump_json(), encoding="utf-8")

    combined = combinePassageReports([path])

    assert [passage.venueId for passage in combined.passages] == ["venue-a", "venue-b"]


def testVenueCliWritesValidatedOutput(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source.json"
    output = tmp_path / "nested" / "combined.json"
    source.write_text(
        "[" + _venue("camden-1", "Camden").model_dump_json() + "]",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["dataset_combiner", "venues", str(output), str(source)],
    )

    assert dataset_combiner.main() == 0
    assert [venue.venueId for venue in combineVenues([output])] == ["camden-1"]
    assert "Combined 1 venues" in capsys.readouterr().out
