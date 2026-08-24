"""Combine validated borough venue datasets and website-passage reports."""

import argparse
from pathlib import Path

from pydantic import TypeAdapter

from app.models.venue import Venue
from app.source_enrichment.website_passage_ingester import (
    WebsitePassageReport,
)


def combineVenues(paths: list[Path]) -> list[Venue]:
    """Load, validate, deduplicate, and sort several borough datasets."""
    venues = [
        venue
        for path in paths
        for venue in TypeAdapter(list[Venue]).validate_json(path.read_text(encoding="utf-8"))
    ]
    venueIds = [venue.venueId for venue in venues]
    if len(set(venueIds)) != len(venueIds):
        raise ValueError("Combined venue datasets contain duplicate venue IDs")
    return sorted(venues, key=lambda venue: (venue.area, venue.name.casefold(), venue.venueId))


def combinePassageReports(paths: list[Path]) -> WebsitePassageReport:
    """Combine reports while removing byte-identical repeated passages."""
    reports = [
        TypeAdapter(WebsitePassageReport).validate_json(path.read_text(encoding="utf-8"))
        for path in paths
    ]
    if not reports:
        raise ValueError("At least one website-passage report is required")
    passagesByKey = {
        (passage.venueId, passage.pageUrl, passage.text): passage
        for report in reports
        for passage in report.passages
    }
    return WebsitePassageReport(
        scannedAt=max(report.scannedAt for report in reports),
        venuesRequested=sum(report.venuesRequested for report in reports),
        venuesScanned=sum(report.venuesScanned for report in reports),
        pagesFetched=sum(report.pagesFetched for report in reports),
        passages=sorted(
            passagesByKey.values(),
            key=lambda passage: (passage.venueId, passage.pageUrl, passage.text),
        ),
        failures=sorted(
            (failure for report in reports for failure in report.failures),
            key=lambda failure: (failure.venueId, failure.website or "", failure.reason),
        ),
    )


def main() -> int:
    """Run one validated combine operation from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="kind", required=True)
    for kind in ("venues", "passages"):
        child = subparsers.add_parser(kind)
        child.add_argument("output", type=Path)
        child.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.kind == "venues":
        venues = combineVenues(args.inputs)
        args.output.write_text(
            TypeAdapter(list[Venue]).dump_json(venues, indent=2).decode() + "\n",
            encoding="utf-8",
        )
        print(f"Combined {len(venues)} venues into {args.output}")
    else:
        report = combinePassageReports(args.inputs)
        args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(f"Combined {len(report.passages)} passages into {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
