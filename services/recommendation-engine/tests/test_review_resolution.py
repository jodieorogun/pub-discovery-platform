"""Manual-review resolution tests."""

import json
import sys
from pathlib import Path

import pytest

from app.source_matching import review_resolution
from app.source_matching.review_resolution import (
    ReviewDecision,
    ReviewDecisionStatus,
    resolveReviews,
)
from app.source_matching.source_models import MatchStatus, PlaceLink, SourceMatchReport


def link(fsqId: str, osmId: str) -> PlaceLink:
    return PlaceLink(
        fsqPlaceId=fsqId,
        osmElementId=osmId,
        matchScore=0.85,
        matchStatus=MatchStatus.likely,
        distanceMetres=3,
        signals=["postcode match"],
    )


def testResolvesApprovedAndRejectedLinks() -> None:
    report = SourceMatchReport(manualReviewCandidates=[link("f1", "o1"), link("f2", "o2")])
    resolved = resolveReviews(
        report,
        [
            ReviewDecision(
                fsqPlaceId="f1",
                osmElementId="o1",
                decision=ReviewDecisionStatus.approve,
                reason="Same address",
            ),
            ReviewDecision(
                fsqPlaceId="f2",
                osmElementId="o2",
                decision=ReviewDecisionStatus.reject,
                reason="Different venue",
            ),
        ],
    )
    assert [item.fsqPlaceId for item in resolved.acceptedLinks] == ["f1"]
    assert resolved.unmatchedFsqPlaceIds == ["f2"]
    assert resolved.unmatchedOsmElementIds == ["o2"]
    assert resolved.manualReviewCandidates == []


def testRequiresCompleteUniqueDecisions() -> None:
    report = SourceMatchReport(manualReviewCandidates=[link("f1", "o1")])
    with pytest.raises(ValueError, match="cover every"):
        resolveReviews(report, [])


def testRejectsDuplicateDecisions() -> None:
    report = SourceMatchReport(manualReviewCandidates=[link("f1", "o1")])
    decision = ReviewDecision(
        fsqPlaceId="f1",
        osmElementId="o1",
        decision=ReviewDecisionStatus.approve,
        reason="Same address",
    )
    with pytest.raises(ValueError, match="duplicate"):
        resolveReviews(report, [decision, decision])


def testCliWritesResolvedReport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reportPath = tmp_path / "report.json"
    decisionsPath = tmp_path / "decisions.json"
    outputPath = tmp_path / "resolved.json"
    reportPath.write_text(
        SourceMatchReport(manualReviewCandidates=[link("f1", "o1")]).model_dump_json(),
        encoding="utf-8",
    )
    decisionsPath.write_text(
        json.dumps(
            [
                {
                    "fsqPlaceId": "f1",
                    "osmElementId": "o1",
                    "decision": "approve",
                    "reason": "Same address",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["review-resolution", str(reportPath), str(decisionsPath), str(outputPath)],
    )

    assert review_resolution.main() == 0
    resolved = SourceMatchReport.model_validate_json(outputPath.read_text(encoding="utf-8"))
    assert [item.fsqPlaceId for item in resolved.acceptedLinks] == ["f1"]
