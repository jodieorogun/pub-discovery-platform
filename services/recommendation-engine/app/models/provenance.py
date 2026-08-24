"""Field-level venue data provenance."""

from datetime import date

from pydantic import BaseModel, Field


class AttributeProvenance(BaseModel):
    """Describe where one canonical venue attribute came from."""

    source: str = Field(min_length=1)
    sourceRecordId: str = Field(min_length=1)
    verifiedAt: date
    confidence: float = Field(ge=0, le=1)
