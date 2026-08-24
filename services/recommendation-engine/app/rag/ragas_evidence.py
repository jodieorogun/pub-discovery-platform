"""Free, deterministic Ragas metrics for exact evidence-chunk IDs."""

from __future__ import annotations

import asyncio
from typing import Protocol


class EvidenceIdScorer(Protocol):
    """Small interface that keeps Ragas optional outside evidence evaluation."""

    def score(self, retrievedIds: list[str], referenceIds: list[str]) -> tuple[float, float]: ...


class RagasIdEvidenceScorer:
    """Score context precision and recall without an LLM, embeddings, or API calls."""

    def score(self, retrievedIds: list[str], referenceIds: list[str]) -> tuple[float, float]:
        if not referenceIds:
            raise ValueError("reference evidence IDs cannot be empty")
        # Ragas returns NaN for empty retrievals. With a non-empty reference
        # set, both metrics are unambiguously zero and should aggregate cleanly.
        if not retrievedIds:
            return 0.0, 0.0
        return asyncio.run(self._score(retrievedIds, referenceIds))

    async def _score(
        self, retrievedIds: list[str], referenceIds: list[str]
    ) -> tuple[float, float]:
        try:
            from ragas import SingleTurnSample

            # Ragas 0.4.3 documents these as public metrics but does not export
            # them from ``collections`` yet. Import their defining modules to
            # avoid the deprecated compatibility export in ``ragas.metrics``.
            from ragas.metrics._context_precision import IDBasedContextPrecision
            from ragas.metrics._context_recall import IDBasedContextRecall
        except ImportError as error:
            raise RuntimeError(
                "Ragas is not installed; run `uv sync --extra rag` first"
            ) from error

        sample = SingleTurnSample(
            retrieved_context_ids=retrievedIds,
            reference_context_ids=referenceIds,
        )
        precision = await IDBasedContextPrecision().single_turn_ascore(sample)
        recall = await IDBasedContextRecall().single_turn_ascore(sample)
        return float(precision), float(recall)
