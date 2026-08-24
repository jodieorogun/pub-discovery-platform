"""Canonical venue builder tests."""

import json
import sys
from datetime import date
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app.data_pipeline import canonical_venue_builder
from app.data_pipeline.canonical_venue_builder import (
    VenueOverride,
    applyOfficialWebsiteVerifications,
    applyPassageFeatureDecisions,
    applyVenueOverrides,
    buildCanonicalVenues,
)
from app.models.venue import Venue
from app.source_enrichment.official_url_verifier import OfficialWebsiteVerification
from app.source_enrichment.passage_feature_review import PassageFeatureDecision
from app.source_matching.source_models import (
    FsqPlaceRecord,
    MatchStatus,
    OsmPlaceRecord,
    PlaceLink,
    SourceMatchReport,
)


def testBuildsFsqPrimaryVenueWithOsmFallback() -> None:
    fsqPlace = FsqPlaceRecord(
        fsqPlaceId="f1",
        name="Example Pub",
        latitude=51.5,
        longitude=-0.1,
        address="1 Example Street",
        priceTier=2,
        categories=["Pub"],
    )
    osmPlace = OsmPlaceRecord(
        osmElementId="node/1",
        name="The Example Pub",
        latitude=51.5,
        longitude=-0.1,
        postcode="SW1A 1AA",
        website="https://example.invalid",
        servesFood=True,
        hasOutdoorSeating=False,
        hasLiveMusic=True,
        dogFriendly=False,
        wheelchairAccessible=True,
        acceptsReservations=True,
        categories=["pub"],
    )
    report = SourceMatchReport(
        acceptedLinks=[
            PlaceLink(
                fsqPlaceId="f1",
                osmElementId="node/1",
                matchScore=0.95,
                matchStatus=MatchStatus.definite,
                distanceMetres=1,
                signals=["name similarity 0.9"],
            )
        ]
    )
    venue = buildCanonicalVenues([fsqPlace], [osmPlace], report, "Westminster", date(2026, 8, 7))[0]
    assert venue.name == "Example Pub"
    assert venue.address == "1 Example Street"
    assert venue.postcode == "SW1A 1AA"
    assert venue.website == "https://example.invalid"
    assert venue.priceTier == 2
    assert venue.priceLevel == "moderate"
    assert venue.servesFood is True
    assert venue.hasOutdoorSeating is False
    assert venue.hasLiveMusic is True
    assert venue.dogFriendly is False
    assert venue.wheelchairAccessible is True
    assert venue.acceptsReservations is True
    assert venue.attributeProvenance["website"].source == "OpenStreetMap"
    assert venue.attributeProvenance["servesFood"].source == "OpenStreetMap"
    assert venue.attributeProvenance["priceTier"].source == "FSQ Places"


def testCanBuildExplicitOsmOnlyVenue() -> None:
    osmPlace = OsmPlaceRecord(
        osmElementId="node/22",
        name="Camden Example Arms",
        latitude=51.54,
        longitude=-0.14,
        website="https://example.invalid",
        servesFood=True,
        wheelchairAccessible=False,
        categories=["pub"],
    )
    report = SourceMatchReport(unmatchedOsmElementIds=["node/22"])

    venues = buildCanonicalVenues(
        [],
        [osmPlace],
        report,
        "Camden",
        date(2026, 8, 10),
        includeUnmatchedOsm=True,
    )

    assert len(venues) == 1
    assert venues[0].venueId == "osm-node-22"
    assert venues[0].dataSource == "OpenStreetMap"
    assert venues[0].servesFood is True
    assert venues[0].wheelchairAccessible is False
    assert venues[0].attributeProvenance["website"].source == "OpenStreetMap"
    assert venues[0].sourceUrl == "https://www.openstreetmap.org/node/22"


def testCliBuildsCanonicalVenueFile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fsqPath = tmp_path / "fsq.json"
    osmPath = tmp_path / "osm.json"
    reportPath = tmp_path / "resolved.json"
    outputPath = tmp_path / "venues.json"
    overridesPath = tmp_path / "overrides.json"
    fsqPlace = FsqPlaceRecord(
        fsqPlaceId="f1",
        name="Example Pub",
        latitude=51.5,
        longitude=-0.1,
        categories=["Pub"],
    )
    osmPlace = OsmPlaceRecord(
        osmElementId="node/1",
        name="The Example Pub",
        latitude=51.5,
        longitude=-0.1,
        categories=["pub"],
    )
    report = SourceMatchReport(
        acceptedLinks=[
            PlaceLink(
                fsqPlaceId="f1",
                osmElementId="node/1",
                matchScore=0.95,
                matchStatus=MatchStatus.definite,
                distanceMetres=1,
                signals=["name similarity 0.9"],
            )
        ]
    )
    fsqPath.write_text(
        TypeAdapter(list[FsqPlaceRecord]).dump_json([fsqPlace]).decode(),
        encoding="utf-8",
    )
    osmPath.write_text(
        TypeAdapter(list[OsmPlaceRecord]).dump_json([osmPlace]).decode(),
        encoding="utf-8",
    )
    reportPath.write_text(report.model_dump_json(), encoding="utf-8")
    overridesPath.write_text(
        TypeAdapter(list[VenueOverride])
        .dump_json(
            [
                VenueOverride(
                    venueId="fsq-f1",
                    evidenceUrl="https://official.example.invalid",
                    fieldEvidence={"phone": "https://official.example.invalid/contact"},
                    website="https://official.example.invalid",
                    phone="+442071234567",
                    reason="Official site confirms the venue address",
                    verifiedAt=date(2026, 8, 8),
                )
            ]
        )
        .decode(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canonical-venue-builder",
            str(fsqPath),
            str(osmPath),
            str(reportPath),
            str(outputPath),
            "--area",
            "Westminster",
            "--verified-at",
            "2026-08-07",
            "--overrides",
            str(overridesPath),
        ],
    )

    assert canonical_venue_builder.main() == 0
    venues = TypeAdapter(list[Venue]).validate_json(outputPath.read_text(encoding="utf-8"))
    assert [venue.venueId for venue in venues] == ["fsq-f1"]
    assert venues[0].website == "https://official.example.invalid"
    assert venues[0].phone == "+442071234567"
    assert venues[0].attributeProvenance["website"].source == "Official venue website"
    assert (
        venues[0].attributeProvenance["phone"].sourceRecordId
        == "https://official.example.invalid/contact"
    )


def testVenueOverridesRejectInvalidRecords() -> None:
    venue = Venue(
        venueId="venue-1",
        name="Example",
        latitude=51.5,
        longitude=-0.1,
        area="Westminster",
        tags=["pub"],
    )
    override = VenueOverride(
        venueId="venue-1",
        evidenceUrl="https://official.example.invalid",
        website="https://official.example.invalid",
        reason="Official site",
        verifiedAt=date(2026, 8, 8),
    )
    with pytest.raises(ValueError, match="duplicate"):
        applyVenueOverrides([venue], [override, override])
    with pytest.raises(ValueError, match="unknown"):
        applyVenueOverrides(
            [venue],
            [override.model_copy(update={"venueId": "missing"})],
        )
    with pytest.raises(ValueError, match="at least one"):
        VenueOverride(
            venueId="venue-1",
            evidenceUrl="https://official.example.invalid",
            reason="Audit found no correction",
            verifiedAt=date(2026, 8, 8),
        )
    with pytest.raises(ValueError, match="Field evidence"):
        VenueOverride(
            venueId="venue-1",
            evidenceUrl="https://official.example.invalid",
            fieldEvidence={"phone": "https://official.example.invalid/contact"},
            website="https://official.example.invalid",
            reason="Evidence field does not match the correction",
            verifiedAt=date(2026, 8, 8),
        )


def testPromotesVerifiedWebsiteProvenanceWithoutChangingOtherFacts() -> None:
    venue = Venue(
        venueId="venue-1",
        name="Example",
        latitude=51.5,
        longitude=-0.1,
        area="Westminster",
        website="https://old.example",
        servesFood=None,
        tags=["pub"],
    )
    verification = OfficialWebsiteVerification(
        venueId="venue-1",
        website="https://official.example/pub",
        evidenceUrl="https://official.example/pub",
        verifiedAt=date(2026, 8, 10),
        reason="Venue identity appears in the URL and page title",
    )

    result = applyOfficialWebsiteVerifications([venue], [verification])[0]

    assert result.website == "https://official.example/pub"
    assert result.servesFood is None
    assert result.attributeProvenance["website"].source == "Official venue website"
    assert result.attributeProvenance["website"].confidence == 1.0


def testAppliesOnlyApprovedPassageFeatureDecisions() -> None:
    venue = Venue(
        venueId="venue-1",
        name="Example",
        latitude=51.5,
        longitude=-0.1,
        area="Westminster",
        tags=["pub"],
    )
    approved = PassageFeatureDecision(
        venueId="venue-1",
        venueName="Example",
        feature="showsSports",
        suggestedValue=True,
        evidenceUrl="https://official.example/sport",
        evidenceText="Watch live sport every weekend.",
        reviewedAt=date(2026, 8, 10),
        reviewStatus="approved",
        reviewReason="Explicit official statement",
    )
    rejected = approved.model_copy(update={"feature": "hasLiveMusic", "reviewStatus": "rejected"})

    result = applyPassageFeatureDecisions([venue], [approved, rejected])[0]

    assert result.showsSports is True
    assert result.hasLiveMusic is None
    assert result.attributeProvenance["showsSports"].sourceRecordId.endswith("/sport")


def testWestminsterAuditAndOverridesStayInSync() -> None:
    repositoryRoot = Path(__file__).parents[1]
    audit = json.loads(
        (repositoryRoot / "data/audits/westminster_top20_2026-08-08.json").read_text(
            encoding="utf-8"
        )
    )
    overrides = TypeAdapter(list[VenueOverride]).validate_json(
        (repositoryRoot / "data/overrides/westminster_venue_overrides.json").read_text(
            encoding="utf-8"
        )
    )

    assert [entry["rank"] for entry in audit] == list(range(1, 21))
    assert {entry["auditStatus"] for entry in audit} == {"verified"}
    assert len({entry["venueId"] for entry in audit}) == 20
    auditCorrections = {
        entry["venueId"]: set(entry["corrections"]) for entry in audit if entry["corrections"]
    }
    metadataFields = {
        "venueId",
        "evidenceUrl",
        "fieldEvidence",
        "reason",
        "verifiedAt",
    }
    overrideCorrections = {
        override.venueId: {
            field
            for field, value in override.model_dump().items()
            if field not in metadataFields and value is not None
        }
        for override in overrides
    }
    assert auditCorrections == overrideCorrections
