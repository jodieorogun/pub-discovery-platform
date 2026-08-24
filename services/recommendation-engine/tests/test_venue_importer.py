"""Venue dataset importer and quality report tests."""

import csv
import json
from pathlib import Path

import pytest

from app.data_pipeline.venue_importer import REQUIRED_COLUMNS, VenueDatasetImporter


def makeRow(**overrides: str) -> dict[str, str]:
    """Return one valid import row."""
    row = {
        "venueId": "venue-real-001",
        "name": "The Verified Arms",
        "latitude": "51.5033",
        "longitude": "-0.1147",
        "area": "Waterloo",
        "priceLevel": "moderate",
        "servesFood": "true",
        "hasOutdoorSeating": "true",
        "showsSports": "false",
        "suitableForGroups": "true",
        "noiseLevel": "moderate",
        "rating": "4.4",
        "popularityScore": "0.8",
        "tags": "food|garden",
        "dataSource": "curated-test-source",
        "sourceUrl": "https://example.com/venue",
        "lastVerifiedDate": "2026-08-05",
    }
    row.update(overrides)
    return row


def writeCsv(dataPath: Path, rows: list[dict[str, str]]) -> None:
    """Write rows using the complete import header."""
    with dataPath.open("w", encoding="utf-8", newline="") as dataFile:
        writer = csv.DictWriter(dataFile, fieldnames=sorted(REQUIRED_COLUMNS | {"sourceUrl"}))
        writer.writeheader()
        writer.writerows(rows)


def testImportsCleanDatasetAndWritesReport(tmp_path: Path) -> None:
    inputPath = tmp_path / "venues.csv"
    outputPath = tmp_path / "venues.json"
    reportPath = tmp_path / "quality.json"
    writeCsv(inputPath, [makeRow()])

    report = VenueDatasetImporter().importCsv(inputPath, outputPath, reportPath)

    assert report.imported is True
    assert report.isClean is True
    assert (report.totalRows, report.validRows) == (1, 1)
    venue = json.loads(outputPath.read_text(encoding="utf-8"))[0]
    assert venue["dataSource"] == "curated-test-source"
    assert venue["tags"] == ["food", "garden"]
    assert json.loads(reportPath.read_text(encoding="utf-8"))["imported"] is True


def testRejectsInvalidAndDuplicateRowsWithoutOverwritingOutput(tmp_path: Path) -> None:
    inputPath = tmp_path / "venues.csv"
    outputPath = tmp_path / "venues.json"
    outputPath.write_text("existing dataset", encoding="utf-8")
    writeCsv(
        inputPath,
        [
            makeRow(),
            makeRow(venueId="venue-duplicate"),
            makeRow(venueId="venue-invalid", name="Invalid", rating="9"),
        ],
    )

    report = VenueDatasetImporter().importCsv(inputPath, outputPath)

    assert report.imported is False
    assert report.invalidRows == 1
    assert report.duplicateRows == 1
    assert {issue.issueType for issue in report.issues} == {"duplicate", "validation"}
    assert outputPath.read_text(encoding="utf-8") == "existing dataset"


def testDetectsDuplicateVenueId(tmp_path: Path) -> None:
    inputPath = tmp_path / "venues.csv"
    writeCsv(inputPath, [makeRow(), makeRow(name="Another Venue", area="Camden")])

    _, report = VenueDatasetImporter().validateCsv(inputPath)

    assert report.duplicateRows == 1


def testRejectsMissingRequiredHeaders(tmp_path: Path) -> None:
    inputPath = tmp_path / "venues.csv"
    inputPath.write_text("venueId,name\nvenue-1,Incomplete\n", encoding="utf-8")

    with pytest.raises(ValueError, match="missing required columns"):
        VenueDatasetImporter().validateCsv(inputPath)
