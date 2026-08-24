"""Unknown-aware retrieval and ranking tests."""

from datetime import date

from app.models.provenance import AttributeProvenance
from app.models.venue import Venue
from app.ranking.venue_ranker import rankVenues
from app.retrieval.candidate_retriever import retrieveCandidates
from app.schemas.recommendation import ParsedPreferences


def makeUnknownVenue() -> Venue:
    """Build a source venue without unverified recommendation attributes."""
    return Venue(
        venueId="fsq-example",
        name="Unknown Attributes Pub",
        latitude=51.5033,
        longitude=-0.1147,
        area="Waterloo",
        tags=["pub"],
        dataSource="FSQ OS Places",
        attributeProvenance={
            "name": AttributeProvenance(
                source="fsq",
                sourceRecordId="fsq-example",
                verifiedAt=date(2026, 8, 7),
                confidence=0.95,
            )
        },
    )


def testUnknownHardRequirementDoesNotPretendToMatch() -> None:
    venue = makeUnknownVenue()

    assert retrieveCandidates([venue], ParsedPreferences(requiresFood=True)) == []
    assert retrieveCandidates([venue], ParsedPreferences()) == [venue]


def testUnknownQualitySignalsDoNotGenerateUnsupportedReasons() -> None:
    recommendation = rankVenues([makeUnknownVenue()], ParsedPreferences(), 1)[0]

    assert recommendation.score == 0
    assert recommendation.reasons == []
