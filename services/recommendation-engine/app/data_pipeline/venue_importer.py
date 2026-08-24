"""Validated CSV-to-JSON venue dataset importer."""

import argparse
import csv
import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.data_pipeline.quality_report import DatasetIssue, DatasetQualityReport
from app.models.venue import Venue

REQUIRED_COLUMNS = {
    "venueId", "name", "latitude", "longitude", "area", "priceLevel",
    "servesFood", "hasOutdoorSeating", "showsSports", "suitableForGroups",
    "noiseLevel", "rating", "popularityScore", "tags", "dataSource",
    "lastVerifiedDate",
}


class VenueImportRecord(BaseModel):
    """Strict input schema for one imported venue row."""

    venueId: str = Field(min_length=1)
    name: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    area: str = Field(min_length=1)
    priceLevel: str
    servesFood: bool
    hasOutdoorSeating: bool
    showsSports: bool
    suitableForGroups: bool
    noiseLevel: str
    rating: float = Field(ge=0, le=5)
    popularityScore: float = Field(ge=0, le=1)
    tags: list[str]
    dataSource: str = Field(min_length=1)
    sourceUrl: str | None = None
    lastVerifiedDate: date

    @field_validator("priceLevel")
    @classmethod
    def validatePriceLevel(cls, value: str) -> str:
        """Restrict prices to values understood by the ranker."""
        if value not in {"cheap", "moderate", "expensive"}:
            raise ValueError("must be cheap, moderate, or expensive")
        return value

    @field_validator("noiseLevel")
    @classmethod
    def validateNoiseLevel(cls, value: str) -> str:
        """Restrict noise levels to supported ranking values."""
        if value not in {"quiet", "moderate", "lively"}:
            raise ValueError("must be quiet, moderate, or lively")
        return value

    def toVenue(self) -> Venue:
        """Convert a validated import record to the domain model."""
        return Venue.model_validate(self.model_dump())


class VenueDatasetImporter:
    """Validate, de-duplicate, and publish a venue CSV dataset."""

    def importCsv(
        self,
        inputPath: Path,
        outputPath: Path,
        reportPath: Path | None = None,
    ) -> DatasetQualityReport:
        """Import a clean dataset; never overwrite output when issues exist."""
        records, report = self.validateCsv(inputPath)
        if report.isClean:
            outputPath.parent.mkdir(parents=True, exist_ok=True)
            outputPath.write_text(
                json.dumps(
                    [record.toVenue().model_dump(mode="json") for record in records],
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            report.imported = True
        if reportPath is not None:
            reportPath.parent.mkdir(parents=True, exist_ok=True)
            reportPath.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return report

    def validateCsv(self, inputPath: Path) -> tuple[list[VenueImportRecord], DatasetQualityReport]:
        """Validate rows and report duplicate IDs and venue identities."""
        records: list[VenueImportRecord] = []
        issues: list[DatasetIssue] = []
        seenVenueIds: set[str] = set()
        seenIdentities: set[tuple[str, str]] = set()
        duplicateRows = 0
        with inputPath.open(encoding="utf-8-sig", newline="") as inputFile:
            reader = csv.DictReader(inputFile)
            self.validateHeaders(reader.fieldnames)
            rows = list(reader)
        for rowNumber, row in enumerate(rows, start=2):
            venueId = row.get("venueId") or None
            try:
                record = self.parseRow(row)
            except (ValidationError, ValueError) as error:
                issues.append(
                    DatasetIssue(
                        rowNumber=rowNumber,
                        venueId=venueId,
                        issueType="validation",
                        message=self.formatError(error),
                    )
                )
                continue
            identity = (record.name.casefold().strip(), record.area.casefold().strip())
            if record.venueId in seenVenueIds or identity in seenIdentities:
                duplicateRows += 1
                issues.append(
                    DatasetIssue(
                        rowNumber=rowNumber,
                        venueId=record.venueId,
                        issueType="duplicate",
                        message="duplicate venueId or normalised name and area",
                    )
                )
                continue
            seenVenueIds.add(record.venueId)
            seenIdentities.add(identity)
            records.append(record)
        invalidRows = sum(issue.issueType == "validation" for issue in issues)
        return records, DatasetQualityReport(
            totalRows=len(rows),
            validRows=len(records),
            invalidRows=invalidRows,
            duplicateRows=duplicateRows,
            imported=False,
            issues=issues,
        )

    @staticmethod
    def validateHeaders(fieldNames: Sequence[str] | None) -> None:
        """Reject files missing fields required for trustworthy records."""
        missingColumns = sorted(REQUIRED_COLUMNS - set(fieldNames or []))
        if missingColumns:
            raise ValueError(f"missing required columns: {', '.join(missingColumns)}")

    @staticmethod
    def parseRow(row: Mapping[str, str | None]) -> VenueImportRecord:
        """Normalise CSV-specific values before Pydantic validation."""
        values: dict[str, Any] = dict(row)
        values["tags"] = [
            tag.strip() for tag in (row.get("tags") or "").split("|") if tag.strip()
        ]
        values["sourceUrl"] = row.get("sourceUrl") or None
        return VenueImportRecord.model_validate(values)

    @staticmethod
    def formatError(error: ValidationError | ValueError) -> str:
        """Return a concise report message without losing field context."""
        if isinstance(error, ValidationError):
            return "; ".join(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                for item in error.errors()
            )
        return str(error)


def main() -> int:
    """Run the importer as a command-line module."""
    parser = argparse.ArgumentParser(description="Validate and import a venue CSV dataset")
    parser.add_argument("inputPath", type=Path)
    parser.add_argument("outputPath", type=Path)
    parser.add_argument("--report", dest="reportPath", type=Path)
    arguments = parser.parse_args()
    report = VenueDatasetImporter().importCsv(
        arguments.inputPath, arguments.outputPath, arguments.reportPath
    )
    print(report.model_dump_json(indent=2))
    return 0 if report.imported else 1


if __name__ == "__main__":
    raise SystemExit(main())
