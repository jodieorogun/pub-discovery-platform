"""Apply explicit human decisions to a source-match review queue."""

import argparse
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, TypeAdapter

from app.source_matching.source_models import SourceMatchReport


class ReviewDecisionStatus(StrEnum):
    """Allowed decisions for an uncertain source link."""

    approve = "approve"
    reject = "reject"


class ReviewDecision(BaseModel):
    """An auditable decision for one FSQ-to-OSM candidate."""

    fsqPlaceId: str
    osmElementId: str
    decision: ReviewDecisionStatus
    reason: str = Field(min_length=1)


def resolveReviews(
    report: SourceMatchReport,
    decisions: list[ReviewDecision],
) -> SourceMatchReport:
    """Move reviewed links into accepted or unmatched output sets."""
    decisionsByPair = {(item.fsqPlaceId, item.osmElementId): item for item in decisions}
    if len(decisionsByPair) != len(decisions):
        raise ValueError("Review decisions contain duplicate source pairs")
    expectedPairs = {
        (link.fsqPlaceId, link.osmElementId) for link in report.manualReviewCandidates
    }
    if set(decisionsByPair) != expectedPairs:
        raise ValueError("Review decisions must cover every manual-review candidate exactly")

    approved = []
    rejectedFsqIds: set[str] = set()
    rejectedOsmIds: set[str] = set()
    for link in report.manualReviewCandidates:
        decision = decisionsByPair[(link.fsqPlaceId, link.osmElementId)]
        if decision.decision is ReviewDecisionStatus.approve:
            approved.append(link)
        else:
            rejectedFsqIds.add(link.fsqPlaceId)
            rejectedOsmIds.add(link.osmElementId)
    return SourceMatchReport(
        acceptedLinks=[*report.acceptedLinks, *approved],
        manualReviewCandidates=[],
        unmatchedFsqPlaceIds=sorted(set(report.unmatchedFsqPlaceIds) | rejectedFsqIds),
        unmatchedOsmElementIds=sorted(set(report.unmatchedOsmElementIds) | rejectedOsmIds),
    )


def main() -> int:
    """Resolve a match report using a complete JSON decision file."""
    parser = argparse.ArgumentParser(description="Resolve source-match manual reviews")
    parser.add_argument("matchReportPath", type=Path)
    parser.add_argument("decisionsPath", type=Path)
    parser.add_argument("outputPath", type=Path)
    arguments = parser.parse_args()
    report = SourceMatchReport.model_validate_json(
        arguments.matchReportPath.read_text(encoding="utf-8")
    )
    decisions = TypeAdapter(list[ReviewDecision]).validate_json(
        arguments.decisionsPath.read_text(encoding="utf-8")
    )
    resolved = resolveReviews(report, decisions)
    arguments.outputPath.write_text(resolved.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"Resolved {len(decisions)} decisions; {len(resolved.acceptedLinks)} links accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
