"""Integrity checks for the sealed, deliberately unrun holdout."""

import hashlib
import json
from pathlib import Path

from pydantic import TypeAdapter

from app.rag.evaluate_retrieval import RetrievalEvaluationCase


def testCurrentHoldoutIsSealedAndChecksummed() -> None:
    directory = Path("data/evaluation/current_holdout")
    suitePath = directory / "holdout_v9.json"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    cases = TypeAdapter(list[RetrievalEvaluationCase]).validate_json(
        suitePath.read_text(encoding="utf-8")
    )

    assert manifest["status"] == "sealed-unrun"
    assert manifest["caseCount"] == len(cases) == 24
    assert manifest["sha256"] == hashlib.sha256(suitePath.read_bytes()).hexdigest()
    assert len({case.query.casefold() for case in cases}) == len(cases)
