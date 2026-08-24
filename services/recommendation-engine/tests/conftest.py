"""Shared test fixtures."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Provide a client with application lifespan events enabled."""
    monkeypatch.setenv("FEEDBACK_DB_PATH", str(tmp_path / "feedback.sqlite3"))
    monkeypatch.setenv("ACCOUNT_DB_PATH", str(tmp_path / "accounts.sqlite3"))
    monkeypatch.setenv("PRICE_DB_PATH", str(tmp_path / "price_observations.sqlite3"))
    with TestClient(app) as testClient:
        yield testClient
