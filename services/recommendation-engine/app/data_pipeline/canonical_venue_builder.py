"""Build canonical API venues from resolved FSQ-to-OSM links."""

import argparse
from datetime import date
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, Field, TypeAdapter, model_validator

from app.models.provenance import AttributeProvenance
from app.models.venue import Venue
from app.source_enrichment.official_url_verifier import OfficialWebsiteVerification
from app.source_enrichment.passage_feature_review import PassageFeatureDecision
from app.source_matching.source_models import FsqPlaceRecord, OsmPlaceRecord, SourceMatchReport

EvidenceUrl = Annotated[str, Field(pattern=r"^https?://", min_length=1)]


def priceLevelFromTier(priceTier: int | None) -> str | None:
    """Map FSQ's retained four-tier price signal to the query vocabulary."""
    if priceTier is None:
        return None
    return {1: "cheap", 2: "moderate", 3: "expensive", 4: "expensive"}[priceTier]


class VenueOverride(BaseModel):
    """A reviewed correction that supersedes stale source attributes."""

    venueId: str = Field(min_length=1)
    evidenceUrl: EvidenceUrl
    fieldEvidence: dict[str, EvidenceUrl] = Field(default_factory=dict)
    reason: str = Field(min_length=1)
    verifiedAt: date
    name: str | None = Field(default=None, min_length=1)
    address: str | None = Field(default=None, min_length=1)
    postcode: str | None = Field(default=None, min_length=1)
    website: str | None = Field(default=None, pattern=r"^https?://", min_length=1)
    phone: str | None = Field(default=None, min_length=1)
    servesFood: bool | None = None
    hasOutdoorSeating: bool | None = None
    showsSports: bool | None = None
    hasLiveMusic: bool | None = None
    hasDj: bool | None = None
    hostsEvents: bool | None = None
    dogFriendly: bool | None = None
    wheelchairAccessible: bool | None = None
    acceptsReservations: bool | None = None
    suitableForGroups: bool | None = None
    hasHappyHour: bool | None = None
    servesSundayRoast: bool | None = None
    hasVeganOptions: bool | None = None
    hostsQuiz: bool | None = None
    hasAccessibleToilet: bool | None = None

    @model_validator(mode="after")
    def requireCorrectedAttribute(self) -> Self:
        """Require every audit entry to change at least one canonical attribute."""
        fields = (
            "name",
            "address",
            "postcode",
            "website",
            "phone",
            "servesFood",
            "hasOutdoorSeating",
            "showsSports",
            "hasLiveMusic",
            "hasDj",
            "hostsEvents",
            "dogFriendly",
            "wheelchairAccessible",
            "acceptsReservations",
            "suitableForGroups",
            "hasHappyHour",
            "servesSundayRoast",
            "hasVeganOptions",
            "hostsQuiz",
            "hasAccessibleToilet",
        )
        if not any(getattr(self, field) is not None for field in fields):
            raise ValueError("Venue override must correct at least one attribute")
        correctedFields = {field for field in fields if getattr(self, field) is not None}
        unsupportedEvidence = set(self.fieldEvidence) - correctedFields
        if unsupportedEvidence:
            raise ValueError(
                f"Field evidence must reference corrected attributes: {sorted(unsupportedEvidence)}"
            )
        return self


def buildCanonicalVenues(
    fsqPlaces: list[FsqPlaceRecord],
    osmPlaces: list[OsmPlaceRecord],
    report: SourceMatchReport,
    area: str,
    verifiedAt: date,
    includeUnmatchedOsm: bool = False,
) -> list[Venue]:
    """Use FSQ as primary data and OSM only to fill missing observable fields."""
    fsqById = {place.fsqPlaceId: place for place in fsqPlaces}
    osmById = {place.osmElementId: place for place in osmPlaces}
    venues: list[Venue] = []
    for link in report.acceptedLinks:
        fsqPlace = fsqById[link.fsqPlaceId]
        osmPlace = osmById[link.osmElementId]
        values = {
            "address": fsqPlace.address or osmPlace.address,
            "postcode": fsqPlace.postcode or osmPlace.postcode,
            "website": fsqPlace.website or osmPlace.website,
            "phone": fsqPlace.phone or osmPlace.phone,
        }
        provenance = {
            field: AttributeProvenance(
                source="FSQ Places" if getattr(fsqPlace, field) else "OpenStreetMap",
                sourceRecordId=(
                    fsqPlace.fsqPlaceId if getattr(fsqPlace, field) else osmPlace.osmElementId
                ),
                verifiedAt=verifiedAt,
                confidence=link.matchScore,
            )
            for field, value in values.items()
            if value is not None
        }
        enrichmentValues = {
            "servesFood": osmPlace.servesFood,
            "hasOutdoorSeating": osmPlace.hasOutdoorSeating,
            "hasLiveMusic": osmPlace.hasLiveMusic,
            "dogFriendly": osmPlace.dogFriendly,
            "wheelchairAccessible": osmPlace.wheelchairAccessible,
            "acceptsReservations": osmPlace.acceptsReservations,
        }
        for field, value in enrichmentValues.items():
            if value is not None:
                provenance[field] = AttributeProvenance(
                    source="OpenStreetMap",
                    sourceRecordId=osmPlace.osmElementId,
                    verifiedAt=verifiedAt,
                    confidence=link.matchScore,
                )
        for field in ("name", "latitude", "longitude"):
            provenance[field] = AttributeProvenance(
                source="FSQ Places",
                sourceRecordId=fsqPlace.fsqPlaceId,
                verifiedAt=verifiedAt,
                confidence=link.matchScore,
            )
        priceLevel = priceLevelFromTier(fsqPlace.priceTier)
        if fsqPlace.priceTier is not None:
            for field in ("priceTier", "priceLevel"):
                provenance[field] = AttributeProvenance(
                    source="FSQ Places",
                    sourceRecordId=fsqPlace.fsqPlaceId,
                    verifiedAt=verifiedAt,
                    confidence=link.matchScore,
                )
        tags = sorted(
            {
                value.casefold().replace(" ", "-")
                for value in [*fsqPlace.categories, *osmPlace.categories]
            }
        )
        venues.append(
            Venue(
                venueId=f"fsq-{fsqPlace.fsqPlaceId}",
                name=fsqPlace.name,
                latitude=fsqPlace.latitude,
                longitude=fsqPlace.longitude,
                area=area,
                tags=tags,
                dataSource="FSQ Places + OpenStreetMap",
                lastVerifiedDate=verifiedAt,
                attributeProvenance=provenance,
                priceTier=fsqPlace.priceTier,
                priceLevel=priceLevel,
                **values,
                **enrichmentValues,
            )
        )

    if includeUnmatchedOsm:
        for osmElementId in report.unmatchedOsmElementIds:
            osmPlace = osmById[osmElementId]
            sourceAttributes: dict[str, str | bool | None] = {
                "address": osmPlace.address,
                "postcode": osmPlace.postcode,
                "website": osmPlace.website,
                "phone": osmPlace.phone,
                "servesFood": osmPlace.servesFood,
                "hasOutdoorSeating": osmPlace.hasOutdoorSeating,
                "hasLiveMusic": osmPlace.hasLiveMusic,
                "dogFriendly": osmPlace.dogFriendly,
                "wheelchairAccessible": osmPlace.wheelchairAccessible,
                "acceptsReservations": osmPlace.acceptsReservations,
            }
            provenance = {
                field: AttributeProvenance(
                    source="OpenStreetMap",
                    sourceRecordId=osmPlace.osmElementId,
                    verifiedAt=verifiedAt,
                    confidence=0.8,
                )
                for field, value in sourceAttributes.items()
                if value is not None
            }
            for field in ("name", "latitude", "longitude"):
                provenance[field] = AttributeProvenance(
                    source="OpenStreetMap",
                    sourceRecordId=osmPlace.osmElementId,
                    verifiedAt=verifiedAt,
                    confidence=0.8,
                )
            tags = sorted(
                value.casefold().replace(" ", "-") for value in osmPlace.categories
            )
            venues.append(
                Venue(
                    venueId=f"osm-{osmPlace.osmElementId.replace('/', '-')}",
                    name=osmPlace.name,
                    latitude=osmPlace.latitude,
                    longitude=osmPlace.longitude,
                    area=area,
                    tags=tags,
                    dataSource="OpenStreetMap",
                    sourceUrl=f"https://www.openstreetmap.org/{osmPlace.osmElementId}",
                    lastVerifiedDate=verifiedAt,
                    attributeProvenance=provenance,
                    address=osmPlace.address,
                    postcode=osmPlace.postcode,
                    website=osmPlace.website,
                    phone=osmPlace.phone,
                    servesFood=osmPlace.servesFood,
                    hasOutdoorSeating=osmPlace.hasOutdoorSeating,
                    hasLiveMusic=osmPlace.hasLiveMusic,
                    dogFriendly=osmPlace.dogFriendly,
                    wheelchairAccessible=osmPlace.wheelchairAccessible,
                    acceptsReservations=osmPlace.acceptsReservations,
                )
            )
    return sorted(venues, key=lambda venue: (venue.name.casefold(), venue.venueId))


def applyVenueOverrides(venues: list[Venue], overrides: list[VenueOverride]) -> list[Venue]:
    """Apply complete, auditable corrections to known canonical venues."""
    overridesByVenueId = {override.venueId: override for override in overrides}
    if len(overridesByVenueId) != len(overrides):
        raise ValueError("Venue overrides contain duplicate venue IDs")
    knownVenueIds = {venue.venueId for venue in venues}
    unknownVenueIds = set(overridesByVenueId) - knownVenueIds
    if unknownVenueIds:
        raise ValueError(f"Venue overrides reference unknown venue IDs: {sorted(unknownVenueIds)}")

    correctedVenues = []
    for venue in venues:
        override = overridesByVenueId.get(venue.venueId)
        if override is None:
            correctedVenues.append(venue)
            continue
        provenance = dict(venue.attributeProvenance)
        correctedFields = {
            field: value
            for field in (
                "name",
                "address",
                "postcode",
                "website",
                "phone",
                "servesFood",
                "hasOutdoorSeating",
                "showsSports",
                "hasLiveMusic",
                "hasDj",
                "hostsEvents",
                "dogFriendly",
                "wheelchairAccessible",
                "acceptsReservations",
                "suitableForGroups",
                "hasHappyHour",
                "servesSundayRoast",
                "hasVeganOptions",
                "hostsQuiz",
                "hasAccessibleToilet",
            )
            if (value := getattr(override, field)) is not None
        }
        for field in correctedFields:
            provenance[field] = AttributeProvenance(
                source="Official venue website",
                sourceRecordId=override.fieldEvidence.get(field, override.evidenceUrl),
                verifiedAt=override.verifiedAt,
                confidence=1.0,
            )
        correctedVenues.append(
            venue.model_copy(
                update={
                    **correctedFields,
                    "sourceUrl": override.evidenceUrl,
                    "lastVerifiedDate": max(
                        venue.lastVerifiedDate or override.verifiedAt,
                        override.verifiedAt,
                    ),
                    "attributeProvenance": provenance,
                }
            )
        )
    return correctedVenues


def applyOfficialWebsiteVerifications(
    venues: list[Venue], verifications: list[OfficialWebsiteVerification]
) -> list[Venue]:
    """Promote reviewed URLs to official provenance without inventing venue facts."""
    byVenueId = {verification.venueId: verification for verification in verifications}
    if len(byVenueId) != len(verifications):
        raise ValueError("Official website verifications contain duplicate venue IDs")
    knownVenueIds = {venue.venueId for venue in venues}
    unknownVenueIds = set(byVenueId) - knownVenueIds
    if unknownVenueIds:
        raise ValueError(
            f"Official website verifications reference unknown venue IDs: {sorted(unknownVenueIds)}"
        )

    verifiedVenues: list[Venue] = []
    for venue in venues:
        verification = byVenueId.get(venue.venueId)
        if verification is None:
            verifiedVenues.append(venue)
            continue
        provenance = dict(venue.attributeProvenance)
        provenance["website"] = AttributeProvenance(
            source="Official venue website",
            sourceRecordId=verification.evidenceUrl,
            verifiedAt=verification.verifiedAt,
            confidence=1.0,
        )
        verifiedVenues.append(
            venue.model_copy(
                update={
                    "website": verification.website,
                    "sourceUrl": verification.evidenceUrl,
                    "lastVerifiedDate": max(
                        venue.lastVerifiedDate or verification.verifiedAt,
                        verification.verifiedAt,
                    ),
                    "attributeProvenance": provenance,
                }
            )
        )
    return verifiedVenues


def applyPassageFeatureDecisions(
    venues: list[Venue], decisions: list[PassageFeatureDecision]
) -> list[Venue]:
    """Apply only approved official-passage facts to canonical venue attributes."""
    approved = [decision for decision in decisions if decision.reviewStatus == "approved"]
    byVenueAndFeature = {(item.venueId, item.feature): item for item in approved}
    if len(byVenueAndFeature) != len(approved):
        raise ValueError("Approved passage decisions contain duplicate venue features")
    knownVenueIds = {venue.venueId for venue in venues}
    unknownVenueIds = {item.venueId for item in approved} - knownVenueIds
    if unknownVenueIds:
        raise ValueError(
            f"Approved passage decisions reference unknown venues: {sorted(unknownVenueIds)}"
        )

    updatedVenues: list[Venue] = []
    for venue in venues:
        venueDecisions = [item for item in approved if item.venueId == venue.venueId]
        if not venueDecisions:
            updatedVenues.append(venue)
            continue
        provenance = dict(venue.attributeProvenance)
        values: dict[str, bool] = {}
        for decision in venueDecisions:
            values[decision.feature] = decision.suggestedValue
            provenance[decision.feature] = AttributeProvenance(
                source="Official venue website",
                sourceRecordId=decision.evidenceUrl,
                verifiedAt=decision.reviewedAt,
                confidence=1.0,
            )
        updatedVenues.append(
            venue.model_copy(
                update={
                    **values,
                    "lastVerifiedDate": max(
                        [venue.lastVerifiedDate or venueDecisions[0].reviewedAt]
                        + [item.reviewedAt for item in venueDecisions]
                    ),
                    "attributeProvenance": provenance,
                }
            )
        )
    return updatedVenues


def main() -> int:
    """Build and write canonical venues from local source files."""
    parser = argparse.ArgumentParser(description="Build canonical linked venues")
    parser.add_argument("fsqPath", type=Path)
    parser.add_argument("osmPath", type=Path)
    parser.add_argument("resolvedReportPath", type=Path)
    parser.add_argument("outputPath", type=Path)
    parser.add_argument("--area", required=True)
    parser.add_argument("--verified-at", required=True, type=date.fromisoformat)
    parser.add_argument("--overrides", type=Path)
    parser.add_argument("--official-websites", type=Path, nargs="+")
    parser.add_argument("--feature-review", type=Path, nargs="+")
    parser.add_argument(
        "--include-unmatched-osm",
        action="store_true",
        help="Create OSM-only venues for unmatched OSM records",
    )
    arguments = parser.parse_args()
    fsqPlaces = TypeAdapter(list[FsqPlaceRecord]).validate_json(
        arguments.fsqPath.read_text(encoding="utf-8")
    )
    osmPlaces = TypeAdapter(list[OsmPlaceRecord]).validate_json(
        arguments.osmPath.read_text(encoding="utf-8")
    )
    report = SourceMatchReport.model_validate_json(
        arguments.resolvedReportPath.read_text(encoding="utf-8")
    )
    venues = buildCanonicalVenues(
        fsqPlaces,
        osmPlaces,
        report,
        arguments.area,
        arguments.verified_at,
        includeUnmatchedOsm=arguments.include_unmatched_osm,
    )
    if arguments.overrides:
        overrides = TypeAdapter(list[VenueOverride]).validate_json(
            arguments.overrides.read_text(encoding="utf-8")
        )
        venues = applyVenueOverrides(venues, overrides)
    if arguments.official_websites:
        verifications = [
            verification
            for path in arguments.official_websites
            for verification in TypeAdapter(list[OfficialWebsiteVerification]).validate_json(
                path.read_text(encoding="utf-8")
            )
        ]
        venues = applyOfficialWebsiteVerifications(venues, verifications)
    if arguments.feature_review:
        featureDecisions = [
            decision
            for path in arguments.feature_review
            for decision in TypeAdapter(list[PassageFeatureDecision]).validate_json(
                path.read_text(encoding="utf-8")
            )
        ]
        # A shared reviewed batch may cover several borough builds. Select the
        # decisions for this canonical dataset; the application function still
        # rejects unknown IDs when called directly.
        venueIds = {venue.venueId for venue in venues}
        featureDecisions = [
            decision for decision in featureDecisions if decision.venueId in venueIds
        ]
        venues = applyPassageFeatureDecisions(venues, featureDecisions)
    arguments.outputPath.parent.mkdir(parents=True, exist_ok=True)
    arguments.outputPath.write_text(
        TypeAdapter(list[Venue]).dump_json(venues, indent=2).decode() + "\n",
        encoding="utf-8",
    )
    print(f"Built {len(venues)} canonical venues at {arguments.outputPath}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
