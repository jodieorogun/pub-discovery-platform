"""Guard the size and balance of the 300-case retrieval regression suite."""

import hashlib
import json
from pathlib import Path

from pydantic import TypeAdapter

from app.rag.evaluate_retrieval import RetrievalEvaluationCase
from app.rag.generate_regression_suite import buildRegressionCases

PROJECT_ROOT = Path(__file__).parents[1]
SUITE_PATH = PROJECT_ROOT / "data/evaluation/regression/queries_300.json"
MANIFEST_PATH = PROJECT_ROOT / "data/evaluation/regression/manifest.json"


def testRegressionSuiteHasThreeHundredUniqueCases() -> None:
    cases = TypeAdapter(list[RetrievalEvaluationCase]).validate_json(
        SUITE_PATH.read_text(encoding="utf-8")
    )

    assert len(cases) == 300
    assert len({case.name for case in cases}) == 300
    assert len({case.query.casefold() for case in cases}) == 300


def testRegressionSuiteHasExpectedCompositionAndChecksum() -> None:
    suiteBytes = SUITE_PATH.read_bytes()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["caseCount"] == 300
    assert sum(manifest["composition"].values()) == 300
    assert manifest["sha256"] == hashlib.sha256(suiteBytes).hexdigest()
    assert "must not be reported as an untouched holdout" in manifest["policy"]


def testGeneratedSuiteMatchesTrackedArtifact() -> None:
    tracked = TypeAdapter(list[RetrievalEvaluationCase]).validate_json(
        SUITE_PATH.read_text(encoding="utf-8")
    )

    assert buildRegressionCases() == tracked


def testRegressionSuiteCoversEveryFeatureAndNegation() -> None:
    cases = buildRegressionCases()
    required = {attribute for case in cases for attribute in case.requiredAttributes}
    excluded = {attribute for case in cases for attribute in case.excludedAttributes}
    queries = " ".join(case.query.casefold() for case in cases)

    assert required == excluded
    assert len(required) == 9
    assert "step-free access" in queries
    assert "no step-free access" in queries
    assert sum(case.expectNoResults for case in cases) == 30
