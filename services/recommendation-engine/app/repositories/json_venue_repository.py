"""JSON-backed venue repository implementation."""

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from app.models.venue import Venue


class JsonVenueRepository:
    """Load and validate venue records from a local JSON file."""

    def __init__(self, dataPath: Path) -> None:
        with dataPath.open(encoding="utf-8") as dataFile:
            rawData: Any = json.load(dataFile)
        self.venues = TypeAdapter(list[Venue]).validate_python(rawData)

    def listVenues(self) -> list[Venue]:
        """Return a copy so callers cannot mutate repository state."""
        return list(self.venues)
