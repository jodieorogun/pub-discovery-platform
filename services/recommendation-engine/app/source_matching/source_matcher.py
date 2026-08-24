"""Deterministic FSQ-to-OSM venue matching and report generation."""

import argparse
import re
from difflib import SequenceMatcher
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from urllib.parse import urlparse

from pydantic import TypeAdapter

from app.source_matching.source_models import (
    FsqPlaceRecord,
    MatchStatus,
    OsmPlaceRecord,
    PlaceLink,
    SourceMatchReport,
)

MAX_CANDIDATE_DISTANCE_METRES = 500.0
DISTANCE_SCORE_CUTOFF_METRES = 200.0
DEFINITE_THRESHOLD = 0.90
LIKELY_THRESHOLD = 0.78
REVIEW_THRESHOLD = 0.60


class SourceMatcher:
    """Match independent source records without merging their raw data."""

    def matchSources(
        self,
        fsqPlaces: list[FsqPlaceRecord],
        osmPlaces: list[OsmPlaceRecord],
    ) -> SourceMatchReport:
        """Return unique definite links and a separate manual-review queue."""
        candidates = [
            self.scorePair(fsqPlace, osmPlace)
            for fsqPlace in fsqPlaces
            for osmPlace in osmPlaces
            if self.isCandidate(fsqPlace, osmPlace)
        ]
        candidates.sort(key=lambda link: link.matchScore, reverse=True)

        acceptedLinks: list[PlaceLink] = []
        assignedFsqIds: set[str] = set()
        assignedOsmIds: set[str] = set()
        for link in candidates:
            if link.matchStatus is not MatchStatus.definite:
                continue
            if link.fsqPlaceId in assignedFsqIds or link.osmElementId in assignedOsmIds:
                continue
            acceptedLinks.append(link)
            assignedFsqIds.add(link.fsqPlaceId)
            assignedOsmIds.add(link.osmElementId)

        reviewCandidates: list[PlaceLink] = []
        reviewedFsqIds: set[str] = set()
        reviewedOsmIds: set[str] = set()
        for link in candidates:
            if link.fsqPlaceId in assignedFsqIds or link.osmElementId in assignedOsmIds:
                continue
            if link.fsqPlaceId in reviewedFsqIds or link.osmElementId in reviewedOsmIds:
                continue
            if link.matchStatus in {MatchStatus.likely, MatchStatus.review}:
                reviewCandidates.append(link)
                reviewedFsqIds.add(link.fsqPlaceId)
                reviewedOsmIds.add(link.osmElementId)

        linkedFsqIds = assignedFsqIds | reviewedFsqIds
        consideredOsmIds = assignedOsmIds | reviewedOsmIds
        return SourceMatchReport(
            acceptedLinks=acceptedLinks,
            manualReviewCandidates=reviewCandidates,
            unmatchedFsqPlaceIds=sorted(
                place.fsqPlaceId for place in fsqPlaces if place.fsqPlaceId not in linkedFsqIds
            ),
            unmatchedOsmElementIds=sorted(
                place.osmElementId
                for place in osmPlaces
                if place.osmElementId not in consideredOsmIds
            ),
        )

    def scorePair(self, fsqPlace: FsqPlaceRecord, osmPlace: OsmPlaceRecord) -> PlaceLink:
        """Score a pair using only observable, explainable signals."""
        distanceMetres = calculateDistanceMetres(fsqPlace, osmPlace)
        nameSimilarity = SequenceMatcher(
            None, normaliseName(fsqPlace.name), normaliseName(osmPlace.name)
        ).ratio()
        weightedSignals: list[tuple[float, float]] = [
            (0.40, nameSimilarity),
            (
                0.30,
                max(0.0, 1.0 - distanceMetres / DISTANCE_SCORE_CUTOFF_METRES),
            ),
        ]
        signals = [f"name similarity {nameSimilarity:.2f}", f"distance {distanceMetres:.0f}m"]

        if fsqPlace.postcode and osmPlace.postcode:
            postcodeMatch = normalisePostcode(fsqPlace.postcode) == normalisePostcode(
                osmPlace.postcode
            )
            weightedSignals.append((0.15, float(postcodeMatch)))
            signals.append("postcode match" if postcodeMatch else "postcode mismatch")
        if fsqPlace.website and osmPlace.website:
            websiteMatch = normaliseDomain(fsqPlace.website) == normaliseDomain(osmPlace.website)
            weightedSignals.append((0.10, float(websiteMatch)))
            signals.append("website match" if websiteMatch else "website mismatch")
        if fsqPlace.phone and osmPlace.phone:
            phoneMatch = normalisePhone(fsqPlace.phone) == normalisePhone(osmPlace.phone)
            weightedSignals.append((0.05, float(phoneMatch)))
            signals.append("phone match" if phoneMatch else "phone mismatch")

        totalWeight = sum(weight for weight, _ in weightedSignals)
        score = sum(weight * value for weight, value in weightedSignals) / totalWeight
        roundedScore = round(score, 3)
        return PlaceLink(
            fsqPlaceId=fsqPlace.fsqPlaceId,
            osmElementId=osmPlace.osmElementId,
            matchScore=roundedScore,
            matchStatus=classifyScore(roundedScore),
            distanceMetres=round(distanceMetres, 1),
            signals=signals,
        )

    @staticmethod
    def isCandidate(fsqPlace: FsqPlaceRecord, osmPlace: OsmPlaceRecord) -> bool:
        """Avoid scoring clearly unrelated records."""
        if calculateDistanceMetres(fsqPlace, osmPlace) <= MAX_CANDIDATE_DISTANCE_METRES:
            return True
        return bool(
            fsqPlace.website
            and osmPlace.website
            and normaliseDomain(fsqPlace.website) == normaliseDomain(osmPlace.website)
        )


def classifyScore(score: float) -> MatchStatus:
    """Classify a transparent match score into an action."""
    if score >= DEFINITE_THRESHOLD:
        return MatchStatus.definite
    if score >= LIKELY_THRESHOLD:
        return MatchStatus.likely
    if score >= REVIEW_THRESHOLD:
        return MatchStatus.review
    return MatchStatus.noMatch


def normaliseName(value: str) -> str:
    """Normalise punctuation, whitespace, and a leading definite article."""
    normalised = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    return normalised.removeprefix("the ")


def normalisePostcode(value: str) -> str:
    """Normalise UK-style postcode spacing and case."""
    return re.sub(r"\s+", "", value).casefold()


def normaliseDomain(value: str) -> str:
    """Normalise a website to its registrable-looking host value."""
    parsedValue = value if "://" in value else f"https://{value}"
    return urlparse(parsedValue).netloc.casefold().removeprefix("www.")


def normalisePhone(value: str) -> str:
    """Normalise telephone formatting while retaining the significant digits."""
    digits = re.sub(r"\D", "", value)
    return digits[-10:]


def calculateDistanceMetres(
    fsqPlace: FsqPlaceRecord, osmPlace: OsmPlaceRecord
) -> float:
    """Calculate Haversine distance between two source records."""
    latitudeDelta = radians(osmPlace.latitude - fsqPlace.latitude)
    longitudeDelta = radians(osmPlace.longitude - fsqPlace.longitude)
    haversineValue = sin(latitudeDelta / 2) ** 2 + (
        cos(radians(fsqPlace.latitude))
        * cos(radians(osmPlace.latitude))
        * sin(longitudeDelta / 2) ** 2
    )
    return 6_371_000.0 * 2 * asin(sqrt(haversineValue))


def main() -> int:
    """Match normalised FSQ and OSM JSON files from the command line."""
    parser = argparse.ArgumentParser(description="Match FSQ and OSM venue source records")
    parser.add_argument("fsqPath", type=Path)
    parser.add_argument("osmPath", type=Path)
    parser.add_argument("outputPath", type=Path)
    arguments = parser.parse_args()
    fsqPlaces = TypeAdapter(list[FsqPlaceRecord]).validate_json(
        arguments.fsqPath.read_text(encoding="utf-8")
    )
    osmPlaces = TypeAdapter(list[OsmPlaceRecord]).validate_json(
        arguments.osmPath.read_text(encoding="utf-8")
    )
    report = SourceMatcher().matchSources(fsqPlaces, osmPlaces)
    arguments.outputPath.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
