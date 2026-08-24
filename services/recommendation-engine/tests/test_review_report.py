"""Manual source-review report tests."""

import json
import sys
from pathlib import Path

import pytest

from app.source_matching import review_report
from app.source_matching.review_report import buildManualReviewReport
from app.source_matching.source_models import (
    FsqPlaceRecord,
    MatchStatus,
    OsmPlaceRecord,
    PlaceLink,
    SourceMatchReport,
)


def testBuildManualReviewReport() -> None:
    fsqPlace = FsqPlaceRecord(
        fsqPlaceId="fsq-1",
        name="Example Arms",
        latitude=51.5,
        longitude=-0.1,
    )
    osmPlace = OsmPlaceRecord(
        osmElementId="node/1",
        name="The Example Arms",
        latitude=51.5,
        longitude=-0.1,
    )
    link = PlaceLink(
        fsqPlaceId="fsq-1",
        osmElementId="node/1",
        matchScore=0.85,
        matchStatus=MatchStatus.likely,
        distanceMetres=2,
        signals=["name similarity 0.90"],
    )

    report = buildManualReviewReport(
        SourceMatchReport(manualReviewCandidates=[link]),
        [fsqPlace],
        [osmPlace],
    )

    assert report.candidateCount == 1
    assert report.candidates[0].fsqPlace.fsqPlaceId == "fsq-1"
    assert report.candidates[0].osmPlace.osmElementId == "node/1"


def testMainWritesReviewFile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fsqPath = tmp_path / "fsq.json"
    osmPath = tmp_path / "osm.json"
    matchPath = tmp_path / "match.json"
    outputPath = tmp_path / "nested" / "review.json"
    fsqPath.write_text(
        '[{"fsqPlaceId":"fsq-1","name":"FSQ Pub","latitude":51.5,"longitude":-0.1}]',
        encoding="utf-8",
    )
    osmPath.write_text(
        '[{"osmElementId":"node/1","name":"OSM Pub","latitude":51.5,"longitude":-0.1}]',
        encoding="utf-8",
    )
    matchPath.write_text(
        SourceMatchReport(
            manualReviewCandidates=[
                PlaceLink(
                    fsqPlaceId="fsq-1",
                    osmElementId="node/1",
                    matchScore=0.8,
                    matchStatus=MatchStatus.likely,
                    distanceMetres=1,
                    signals=["distance 1m"],
                )
            ]
        ).model_dump_json(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["review_report", str(fsqPath), str(osmPath), str(matchPath), str(outputPath)],
    )

    assert review_report.main() == 0
    assert json.loads(outputPath.read_text(encoding="utf-8"))["candidateCount"] == 1
    assert "Wrote 1 manual-review candidates" in capsys.readouterr().out
