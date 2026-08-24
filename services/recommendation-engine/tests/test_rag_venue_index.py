"""Tests for grounded local venue retrieval."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models.provenance import AttributeProvenance
from app.models.venue import Venue
from app.rag.venue_index import (
    EvidenceChunk,
    HybridRagRetriever,
    SqliteEvidenceStore,
    VenueRagIndex,
    buildIndex,
    buildVenueChunks,
    evidenceChunkId,
    loadIndex,
    writeIndex,
)
from app.source_enrichment.website_passage_ingester import WebsitePassage


class FakeEmbeddingProvider:
    modelName = "fake-model"

    def _embed(self, text: str) -> list[float]:
        lowered = text.casefold()
        return [
            float("sport" in lowered or "football" in lowered),
            float("dog" in lowered),
            float("food" in lowered),
            0.1,
        ]

    def embedPassages(self, passages: list[str]) -> list[list[float]]:
        return [self._embed(passage) for passage in passages]

    def embedQuery(self, query: str) -> list[float]:
        return self._embed(query)


def makeVenue(venueId: str = "pub-1", **overrides: object) -> Venue:
    values: dict[str, object] = {
        "venueId": venueId,
        "name": "The Test Pub",
        "latitude": 51.5,
        "longitude": -0.13,
        "area": "Westminster",
        "address": "1 Test Street",
        "postcode": "SW1A 1AA",
        "website": "https://example.com/pub",
        "servesFood": True,
        "hasOutdoorSeating": False,
        "showsSports": True,
        "dogFriendly": True,
        "noiseLevel": "lively",
        "rating": 4.5,
        "tags": ["gastropub", "pub"],
        "dataSource": "FSQ Places",
        "sourceUrl": "https://example.com/source",
        "attributeProvenance": {
            "showsSports": AttributeProvenance(
                source="Official venue website",
                sourceRecordId="https://example.com/sport",
                verifiedAt="2026-08-09",
                confidence=1.0,
            )
        },
    }
    values.update(overrides)
    return Venue.model_validate(values)


def test_chunks_include_grounded_positive_and_explicit_negative_facts() -> None:
    chunks = buildVenueChunks(makeVenue())
    texts = [str(chunk["text"]) for chunk in chunks]

    assert any("shows live sport" in text for text in texts)
    assert any("welcomes dogs" in text for text in texts)
    assert any("does not have outdoor seating" in text for text in texts)
    sportChunk = next(chunk for chunk in chunks if chunk["attributes"] == ["showsSports"])
    assert sportChunk["source"] == "Official venue website"
    assert sportChunk["sourceUrl"] == "https://example.com/sport"

    negativeChunks = buildVenueChunks(makeVenue(dogFriendly=False))
    negativeDogChunk = next(
        chunk for chunk in negativeChunks if chunk["attributes"] == ["dogFriendly"]
    )
    assert "does not allow dogs" in str(negativeDogChunk["text"])


def test_evidence_chunk_ids_are_stable_and_content_sensitive() -> None:
    chunk = EvidenceChunk(
        venueId="pub-1",
        text="Verified sport evidence.",
        source="official",
        attributes=["showsSports"],
        embedding=[1.0],
    )

    assert evidenceChunkId(chunk) == evidenceChunkId(chunk.model_copy())
    assert evidenceChunkId(chunk) != evidenceChunkId(
        chunk.model_copy(update={"text": "Different evidence."})
    )


def test_build_write_load_and_retrieve_index(tmp_path: Path) -> None:
    venuePath = tmp_path / "venues.json"
    venuePath.write_text("[" + makeVenue().model_dump_json() + "]", encoding="utf-8")
    provider = FakeEmbeddingProvider()
    index = buildIndex([makeVenue()], venuePath, provider)
    indexPath = tmp_path / "index.json"
    writeIndex(index, indexPath, [makeVenue()])

    loaded = loadIndex(indexPath, venuePath)
    result = HybridRagRetriever(loaded, provider).retrieve("football with my dog", ["pub-1"])

    assert loaded.venueCount == 1
    assert result["pub-1"].semanticScore > 0.65
    assert any("sport" in text for text in result["pub-1"].texts)
    assert "Official venue website" in result["pub-1"].sources
    assert len(result["pub-1"].chunkIds) == len(result["pub-1"].texts)


def test_sqlite_index_round_trip_uses_compact_vector_storage(tmp_path: Path) -> None:
    venuePath = tmp_path / "venues.json"
    venuePath.write_text("[" + makeVenue().model_dump_json() + "]", encoding="utf-8")
    index = buildIndex([makeVenue()], venuePath, FakeEmbeddingProvider())
    indexPath = tmp_path / "evidence.sqlite3"

    writeIndex(index, indexPath, [makeVenue()])
    loaded = loadIndex(indexPath, venuePath)

    assert loaded.modelName == index.modelName
    assert loaded.websitePassageDataSha256 == index.websitePassageDataSha256
    assert [chunk.model_dump(exclude={"embedding"}) for chunk in loaded.chunks] == [
        chunk.model_dump(exclude={"embedding"}) for chunk in index.chunks
    ]
    for loadedChunk, originalChunk in zip(loaded.chunks, index.chunks, strict=True):
        assert loadedChunk.embedding == pytest.approx(originalChunk.embedding)
    with sqlite3.connect(indexPath) as connection:
        assert connection.execute("SELECT count(*) FROM venues").fetchone() == (1,)


def test_sqlite_store_filters_candidates_and_uses_fts(tmp_path: Path) -> None:
    venuePath = tmp_path / "venues.json"
    venues = [makeVenue("pub-1"), makeVenue("pub-2", name="Second Pub")]
    venuePath.write_text(
        json.dumps([item.model_dump(mode="json") for item in venues]), encoding="utf-8"
    )
    indexPath = tmp_path / "evidence.sqlite3"
    writeIndex(buildIndex(venues, venuePath, FakeEmbeddingProvider()), indexPath)
    store = SqliteEvidenceStore(indexPath, venuePath)

    chunks = store.chunksForVenues({"pub-2"})

    assert chunks
    assert {chunk.venueId for chunk in chunks} == {"pub-2"}
    assert store.lexicalMatches("football", {"pub-2"})
    assert not store.lexicalMatches("football", {"missing"})


def test_lexical_coverage_can_span_multiple_venue_passages() -> None:
    index = VenueRagIndex(
        modelName="fake-model",
        venueDataSha256="unused",
        venueCount=1,
        chunks=[
            EvidenceChunk(
                venueId="pub-1",
                text="This pub shows sports.",
                source="official",
                embedding=[1.0, 0.0, 0.0, 0.1],
            ),
            EvidenceChunk(
                venueId="pub-1",
                text="This pub serves food.",
                source="official",
                embedding=[0.0, 0.0, 1.0, 0.1],
            ),
        ],
    )

    result = HybridRagRetriever(index, FakeEmbeddingProvider(), denseWeight=0.0).retrieve(
        "sports food", ["pub-1"]
    )

    assert result["pub-1"].semanticScore == 1.0


def test_preferred_attributes_remove_generic_evidence_noise() -> None:
    index = VenueRagIndex(
        modelName="fake-model",
        venueDataSha256="unused",
        venueCount=1,
        chunks=[
            EvidenceChunk(
                venueId="pub-1",
                text="A generic Westminster pub.",
                source="source",
                attributes=["name", "area"],
                embedding=[1.0, 0.0, 0.0, 0.1],
            ),
            EvidenceChunk(
                venueId="pub-1",
                text="This pub shows live sport.",
                source="official",
                attributes=["showsSports"],
                embedding=[0.9, 0.0, 0.0, 0.1],
            ),
        ],
    )

    result = HybridRagRetriever(index, FakeEmbeddingProvider()).retrieve(
        "Westminster sports pub",
        ["pub-1"],
        preferredAttributes={"showsSports"},
    )

    assert result["pub-1"].texts == ["This pub shows live sport."]


def test_stale_index_and_model_mismatch_are_rejected(tmp_path: Path) -> None:
    venuePath = tmp_path / "venues.json"
    venuePath.write_text("[]", encoding="utf-8")
    index = buildIndex([], venuePath, FakeEmbeddingProvider())
    indexPath = tmp_path / "index.json"
    writeIndex(index, indexPath)
    venuePath.write_text("[ ]", encoding="utf-8")

    with pytest.raises(ValueError, match="stale"):
        loadIndex(indexPath, venuePath)

    index.modelName = "other"
    with pytest.raises(ValueError, match="do not match"):
        HybridRagRetriever(index, FakeEmbeddingProvider())


def test_invalid_embedding_results_are_rejected(tmp_path: Path) -> None:
    class BrokenProvider(FakeEmbeddingProvider):
        def embedPassages(self, passages: list[str]) -> list[list[float]]:
            return []

    venuePath = tmp_path / "venues.json"
    venuePath.write_text(json.dumps([makeVenue().model_dump(mode="json")]), encoding="utf-8")
    with pytest.raises(ValueError, match="wrong number"):
        buildIndex([makeVenue()], venuePath, BrokenProvider())


def test_index_includes_attributed_official_website_passages(tmp_path: Path) -> None:
    venuePath = tmp_path / "venues.json"
    venuePath.write_text("[" + makeVenue().model_dump_json() + "]", encoding="utf-8")
    passagePath = tmp_path / "passages.json"
    passagePath.write_text("{}", encoding="utf-8")
    passage = WebsitePassage(
        venueId="pub-1",
        venueName="The Test Pub",
        pageUrl="https://example.com/about",
        text="A cosy historic pub with a traditional atmosphere near Parliament.",
        retrievedAt=datetime(2026, 8, 9, tzinfo=UTC),
    )

    index = buildIndex([makeVenue()], venuePath, FakeEmbeddingProvider(), [passage], passagePath)

    websiteChunk = next(
        chunk for chunk in index.chunks if chunk.attributes == ["officialWebsiteText"]
    )
    assert websiteChunk.sourceUrl == "https://example.com/about"
    assert "cosy historic pub" in websiteChunk.text
    assert index.websitePassageDataSha256 is not None


def test_retriever_validates_vector_dimensions() -> None:
    index = VenueRagIndex(
        modelName="fake-model",
        venueDataSha256="hash",
        venueCount=1,
        chunks=[
            EvidenceChunk(
                venueId="pub-1",
                text="evidence",
                source="source",
                embedding=[1.0],
            )
        ],
    )
    with pytest.raises(ValueError, match="different dimensions"):
        HybridRagRetriever(index, FakeEmbeddingProvider()).retrieve("sports", ["pub-1"])
    with pytest.raises(ValueError, match="denseWeight"):
        HybridRagRetriever(index, FakeEmbeddingProvider(), denseWeight=1.1)
