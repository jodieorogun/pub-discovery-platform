"""Tests for the optional free Ragas evidence scorer."""

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.rag.ragas_evidence import RagasIdEvidenceScorer


def test_ragas_id_scorer_uses_non_llm_context_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    class Metric:
        async def single_turn_ascore(self, sample: object) -> float:
            await asyncio.sleep(0)
            return 0.5

    ragas = ModuleType("ragas")
    ragas.SingleTurnSample = lambda **values: SimpleNamespace(**values)  # type: ignore[attr-defined]
    precisionModule = ModuleType("ragas.metrics._context_precision")
    precisionModule.IDBasedContextPrecision = Metric  # type: ignore[attr-defined]
    recallModule = ModuleType("ragas.metrics._context_recall")
    recallModule.IDBasedContextRecall = Metric  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ragas", ragas)
    monkeypatch.setitem(sys.modules, "ragas.metrics._context_precision", precisionModule)
    monkeypatch.setitem(sys.modules, "ragas.metrics._context_recall", recallModule)

    assert RagasIdEvidenceScorer().score(["a"], ["a", "b"]) == (0.5, 0.5)


def test_ragas_id_scorer_requires_reference_ids() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        RagasIdEvidenceScorer().score(["a"], [])


def test_ragas_id_scorer_handles_empty_retrieval_without_nan() -> None:
    assert RagasIdEvidenceScorer().score([], ["reference"]) == (0.0, 0.0)
