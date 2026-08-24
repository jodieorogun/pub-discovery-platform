"""Deterministic FSQ-to-OSM source matching tests."""

from app.source_matching.source_matcher import (
    SourceMatcher,
    classifyScore,
    normaliseDomain,
    normaliseName,
    normalisePhone,
    normalisePostcode,
)
from app.source_matching.source_models import (
    FsqPlaceRecord,
    MatchStatus,
    OsmPlaceRecord,
)


def makeFsqPlace(**overrides: object) -> FsqPlaceRecord:
    """Build a normalised FSQ source record."""
    values: dict[str, object] = {
        "fsqPlaceId": "fsq-001",
        "name": "The Crown",
        "latitude": 51.5033,
        "longitude": -0.1147,
        "address": "10 Example Street",
        "postcode": "SE1 8XX",
        "website": "https://thecrown.example",
        "phone": "+44 20 7123 4567",
        "categories": ["Pub"],
    }
    values.update(overrides)
    return FsqPlaceRecord.model_validate(values)


def makeOsmPlace(**overrides: object) -> OsmPlaceRecord:
    """Build a normalised OSM source record."""
    values: dict[str, object] = {
        "osmElementId": "node/1001",
        "name": "Crown",
        "latitude": 51.50335,
        "longitude": -0.11472,
        "address": "10 Example Street",
        "postcode": "SE18XX",
        "website": "thecrown.example",
        "phone": "020 7123 4567",
        "categories": ["amenity=pub"],
    }
    values.update(overrides)
    return OsmPlaceRecord.model_validate(values)


def testAcceptsStrongUniqueMatch() -> None:
    report = SourceMatcher().matchSources([makeFsqPlace()], [makeOsmPlace()])

    assert len(report.acceptedLinks) == 1
    link = report.acceptedLinks[0]
    assert link.matchStatus is MatchStatus.definite
    assert link.matchScore >= 0.9
    assert "postcode match" in link.signals
    assert "website match" in link.signals
    assert "phone match" in link.signals
    assert report.manualReviewCandidates == []


def testQueuesAmbiguousMatchForManualReview() -> None:
    fsqPlace = makeFsqPlace(
        name="Crown Tavern", postcode=None, website=None, phone=None
    )
    osmPlace = makeOsmPlace(
        name="Crown Arms",
        latitude=51.5038,
        postcode=None,
        website=None,
        phone=None,
    )

    report = SourceMatcher().matchSources([fsqPlace], [osmPlace])

    assert report.acceptedLinks == []
    assert len(report.manualReviewCandidates) == 1
    assert report.manualReviewCandidates[0].matchStatus in {
        MatchStatus.likely,
        MatchStatus.review,
    }


def testLeavesDistantUnrelatedPlacesUnmatched() -> None:
    report = SourceMatcher().matchSources(
        [makeFsqPlace()],
        [makeOsmPlace(latitude=51.52, longitude=-0.08, website=None)],
    )

    assert report.acceptedLinks == []
    assert report.manualReviewCandidates == []
    assert report.unmatchedFsqPlaceIds == ["fsq-001"]
    assert report.unmatchedOsmElementIds == ["node/1001"]


def testDoesNotAssignOneOsmRecordTwice() -> None:
    secondFsqPlace = makeFsqPlace(fsqPlaceId="fsq-002", name="Crown Pub")
    report = SourceMatcher().matchSources(
        [makeFsqPlace(), secondFsqPlace],
        [makeOsmPlace()],
    )

    linkedOsmIds = [link.osmElementId for link in report.acceptedLinks]
    assert len(linkedOsmIds) == len(set(linkedOsmIds))
    assert len(report.acceptedLinks) == 1


def testDoesNotQueueOneOsmRecordForReviewTwice() -> None:
    firstFsqPlace = makeFsqPlace(
        name="Travellers Tavern",
        latitude=51.5007,
        longitude=-0.1,
        postcode=None,
        website=None,
        phone=None,
    )
    secondFsqPlace = makeFsqPlace(
        fsqPlaceId="fsq-002",
        name="Travellers Tavern",
        latitude=51.5008,
        longitude=-0.1,
        postcode=None,
        website=None,
        phone=None,
    )
    osmPlace = makeOsmPlace(
        name="Travellers Tavern",
        latitude=51.5,
        longitude=-0.1,
        postcode=None,
        website=None,
        phone=None,
    )

    report = SourceMatcher().matchSources(
        [firstFsqPlace, secondFsqPlace],
        [osmPlace],
    )

    assert len(report.manualReviewCandidates) == 1
    assert report.manualReviewCandidates[0].fsqPlaceId == "fsq-001"
    assert report.unmatchedFsqPlaceIds == ["fsq-002"]


def testNormalisationAndClassificationHelpers() -> None:
    assert normaliseName("The Crown & Anchor") == "crown anchor"
    assert normalisePostcode("SE1 8XX") == "se18xx"
    assert normaliseDomain("https://www.Example.com/path") == "example.com"
    assert normalisePhone("+44 (0)20 7123 4567") == "2071234567"
    assert classifyScore(0.95) is MatchStatus.definite
    assert classifyScore(0.82) is MatchStatus.likely
    assert classifyScore(0.65) is MatchStatus.review
    assert classifyScore(0.4) is MatchStatus.noMatch
