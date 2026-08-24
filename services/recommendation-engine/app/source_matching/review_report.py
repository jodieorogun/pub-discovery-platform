"""Build a human-readable side-by-side source-match review queue."""

import argparse
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter

from app.source_matching.source_models import (
    FsqPlaceRecord,
    OsmPlaceRecord,
    PlaceLink,
    SourceMatchReport,
)


class ReviewCandidate(BaseModel):
    """One uncertain match with both source records visible."""

    link: PlaceLink
    fsqPlace: FsqPlaceRecord
    osmPlace: OsmPlaceRecord


class ManualReviewReport(BaseModel):
    """Review candidates enriched with their complete normalised records."""

    candidateCount: int = Field(ge=0)
    candidates: list[ReviewCandidate]


def buildManualReviewReport(
    matchReport: SourceMatchReport,
    fsqPlaces: list[FsqPlaceRecord],
    osmPlaces: list[OsmPlaceRecord],
) -> ManualReviewReport:
    """Join review links to source records without merging their attributes."""
    fsqById = {place.fsqPlaceId: place for place in fsqPlaces}
    osmById = {place.osmElementId: place for place in osmPlaces}
    candidates = [
        ReviewCandidate(
            link=link,
            fsqPlace=fsqById[link.fsqPlaceId],
            osmPlace=osmById[link.osmElementId],
        )
        for link in matchReport.manualReviewCandidates
    ]
    return ManualReviewReport(candidateCount=len(candidates), candidates=candidates)


def main() -> int:
    """Create a side-by-side JSON review queue from source files and match report."""
    parser = argparse.ArgumentParser(description="Build source-match manual review file")
    parser.add_argument("fsqPath", type=Path)
    parser.add_argument("osmPath", type=Path)
    parser.add_argument("matchReportPath", type=Path)
    parser.add_argument("outputPath", type=Path)
    arguments = parser.parse_args()

    fsqPlaces = TypeAdapter(list[FsqPlaceRecord]).validate_json(
        arguments.fsqPath.read_text(encoding="utf-8")
    )
    osmPlaces = TypeAdapter(list[OsmPlaceRecord]).validate_json(
        arguments.osmPath.read_text(encoding="utf-8")
    )
    matchReport = SourceMatchReport.model_validate_json(
        arguments.matchReportPath.read_text(encoding="utf-8")
    )
    reviewReport = buildManualReviewReport(matchReport, fsqPlaces, osmPlaces)
    arguments.outputPath.parent.mkdir(parents=True, exist_ok=True)
    arguments.outputPath.write_text(
        reviewReport.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {reviewReport.candidateCount} manual-review candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
