"""Venue dataset quality report models."""

from pydantic import BaseModel, Field


class DatasetIssue(BaseModel):
    """One actionable issue found in an imported dataset row."""

    rowNumber: int = Field(ge=2)
    venueId: str | None = None
    issueType: str
    message: str


class DatasetQualityReport(BaseModel):
    """Summary of validation and duplicate checks for one import."""

    totalRows: int = Field(ge=0)
    validRows: int = Field(ge=0)
    invalidRows: int = Field(ge=0)
    duplicateRows: int = Field(ge=0)
    imported: bool
    issues: list[DatasetIssue] = Field(default_factory=list)

    @property
    def isClean(self) -> bool:
        """Return whether the dataset is safe to publish."""
        return self.invalidRows == 0 and self.duplicateRows == 0
