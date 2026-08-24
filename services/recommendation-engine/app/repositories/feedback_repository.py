"""Feedback repository contract."""

from typing import Protocol

from app.models.feedback import FeedbackEvent


class FeedbackRepository(Protocol):
    """Persist recommendation feedback independently of storage technology."""

    def record(self, event: FeedbackEvent) -> None:
        """Persist one immutable feedback event."""
        ...

    def listEvents(self) -> list[FeedbackEvent]:
        """Return recorded events in insertion order for evaluation."""
        ...
