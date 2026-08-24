"""Fetch OpenStreetMap places from Overpass and normalise them for matching."""

import argparse
from pathlib import Path
from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from app.source_matching.source_models import OsmPlaceRecord

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
OVERPASS_USER_AGENT = "ai-recommendation-engine/0.1 (venue source import)"
OSM_TRUE_VALUES = {
    "yes",
    "only",
    "limited",
    "seasonal",
    "parklet",
    "pedestrian_zone",
    "street",
    "sidewalk",
    "patio",
    "terrace",
    "balcony",
    "veranda",
    "roof",
    "garden",
    "courtyard",
}


class OsmImportError(RuntimeError):
    """A safe, user-facing OSM import failure."""


class OsmPlacesClient:
    """Small Overpass client with injectable HTTP transport for tests."""

    def __init__(
        self,
        *,
        timeoutSeconds: float = 90.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.timeoutSeconds = timeoutSeconds
        self.transport = transport

    def search(self, areaName: str, amenity: str = "pub") -> list[OsmPlaceRecord]:
        """Return named OSM amenities inside an administrative area."""
        if not areaName.strip():
            raise OsmImportError("OSM area name cannot be empty")
        if not amenity.strip():
            raise OsmImportError("OSM amenity cannot be empty")

        query = buildOverpassQuery(areaName, amenity)
        lastFailure = "unknown failure"
        with httpx.Client(
            timeout=self.timeoutSeconds,
            transport=self.transport,
        ) as client:
            for endpoint in OVERPASS_URLS:
                try:
                    response = client.post(
                        endpoint,
                        data={"data": query},
                        headers={
                            "Accept": "application/json",
                            "User-Agent": OVERPASS_USER_AGENT,
                        },
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as error:
                    statusCode = error.response.status_code
                    if statusCode == 429 or statusCode >= 500:
                        lastFailure = f"HTTP {statusCode}"
                        continue
                    raise OsmImportError(f"Overpass returned HTTP {statusCode}") from error
                except httpx.RequestError as error:
                    lastFailure = f"network error: {error}"
                    continue
                return normaliseOsmPayload(response.json())
        raise OsmImportError(
            f"All public Overpass instances failed; last failure: {lastFailure}"
        )


def buildOverpassQuery(areaName: str, amenity: str) -> str:
    """Build a bounded query for nodes, ways, and relations."""
    safeAreaName = escapeOverpassString(areaName)
    safeAmenity = escapeOverpassString(amenity)
    return (
        "[out:json][timeout:60];"
        f'area["boundary"="administrative"]["name"="{safeAreaName}"]->.searchArea;'
        f'nwr["amenity"="{safeAmenity}"]["name"](area.searchArea);'
        "out center tags;"
    )


def escapeOverpassString(value: str) -> str:
    """Escape user input placed inside an Overpass quoted string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def normaliseOsmPayload(payload: Any) -> list[OsmPlaceRecord]:
    """Convert an Overpass response into the matcher's stable source schema."""
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise OsmImportError("Overpass response did not contain an elements list")

    records: list[OsmPlaceRecord] = []
    try:
        for element in payload["elements"]:
            if not isinstance(element, dict):
                raise OsmImportError("Overpass returned a non-object element")
            tags = element.get("tags")
            if not isinstance(tags, dict) or not isinstance(tags.get("name"), str):
                continue
            latitude, longitude = elementCoordinates(element)
            records.append(
                OsmPlaceRecord(
                    osmElementId=f'{element.get("type")}/{element.get("id")}',
                    name=tags["name"],
                    latitude=latitude,
                    longitude=longitude,
                    address=buildAddress(tags),
                    postcode=optionalString(tags, "addr:postcode"),
                    website=firstString(tags, "contact:website", "website", "url"),
                    phone=firstString(tags, "contact:phone", "phone"),
                    servesFood=foodAvailability(tags),
                    hasOutdoorSeating=explicitAvailability(tags, "outdoor_seating"),
                    hasLiveMusic=liveMusicAvailability(tags),
                    dogFriendly=mappedAvailability(tags, "dog", {"yes", "leashed"}),
                    wheelchairAccessible=mappedAvailability(tags, "wheelchair", {"yes"}),
                    acceptsReservations=mappedAvailability(
                        tags,
                        "reservation",
                        {"yes", "required", "recommended"},
                    ),
                    categories=[tags["amenity"]]
                    if isinstance(tags.get("amenity"), str)
                    else [],
                )
            )
    except ValidationError as error:
        raise OsmImportError(f"Overpass returned an invalid element: {error}") from error
    return records


def elementCoordinates(element: dict[str, Any]) -> tuple[object, object]:
    """Read node coordinates or the centre returned for a way/relation."""
    if "lat" in element and "lon" in element:
        return element["lat"], element["lon"]
    center = element.get("center")
    if isinstance(center, dict) and "lat" in center and "lon" in center:
        return center["lat"], center["lon"]
    raise OsmImportError(
        f'OSM element {element.get("type")}/{element.get("id")} has no coordinates'
    )


def buildAddress(tags: dict[str, Any]) -> str | None:
    """Build a readable address without inventing missing OSM tags."""
    fullAddress = optionalString(tags, "addr:full")
    if fullAddress:
        return fullAddress
    houseNumber = optionalString(tags, "addr:housenumber")
    street = optionalString(tags, "addr:street")
    return " ".join(part for part in (houseNumber, street) if part) or None


def firstString(tags: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty string for alternative OSM keys."""
    return next((value for key in keys if (value := optionalString(tags, key))), None)


def optionalString(tags: dict[str, Any], key: str) -> str | None:
    """Return a non-empty string tag or None."""
    value = tags.get(key)
    return value if isinstance(value, str) and value.strip() else None


def explicitAvailability(tags: dict[str, Any], key: str) -> bool | None:
    """Convert only documented affirmative/negative OSM values to a boolean."""
    value = optionalString(tags, key)
    if value is None:
        return None
    normalisedValues = {part.strip().casefold() for part in value.split(";")}
    if normalisedValues == {"no"}:
        return False
    if normalisedValues & OSM_TRUE_VALUES:
        return True
    return None


def mappedAvailability(
    tags: dict[str, Any], key: str, trueValues: set[str]
) -> bool | None:
    """Map feature-specific OSM values without treating ambiguous values as true."""
    value = optionalString(tags, key)
    if value is None:
        return None
    normalisedValues = {part.strip().casefold() for part in value.split(";")}
    if normalisedValues == {"no"}:
        return False
    if normalisedValues & trueValues:
        return True
    return None


def liveMusicAvailability(tags: dict[str, Any]) -> bool | None:
    """Read the two established live-music tag spellings, including explicit no."""
    primary = mappedAvailability(tags, "live_music", {"yes"})
    return primary if primary is not None else mappedAvailability(tags, "music:live", {"yes"})


def foodAvailability(tags: dict[str, Any]) -> bool | None:
    """Use explicit food tags, with cuisine as positive evidence of served food."""
    explicitFood = explicitAvailability(tags, "food")
    if explicitFood is not None:
        return explicitFood
    return True if optionalString(tags, "cuisine") else None


def writeOsmPlaces(places: list[OsmPlaceRecord], outputPath: Path) -> None:
    """Write normalized OSM records as a separate source dataset."""
    outputPath.parent.mkdir(parents=True, exist_ok=True)
    outputPath.write_text(
        TypeAdapter(list[OsmPlaceRecord]).dump_json(places, indent=2).decode() + "\n",
        encoding="utf-8",
    )


def main() -> int:
    """Fetch and write an OSM source file from the command line."""
    parser = argparse.ArgumentParser(description="Import OSM places through Overpass")
    parser.add_argument("--area", required=True, help="Exact OSM administrative area name")
    parser.add_argument("--amenity", default="pub", help="OSM amenity value")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    try:
        places = OsmPlacesClient().search(arguments.area, arguments.amenity)
        writeOsmPlaces(places, arguments.output)
    except OsmImportError as error:
        parser.error(str(error))
    print(f"Imported {len(places)} OSM places to {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
