"""Build and query a small local embedding index of grounded venue facts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import struct
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel, Field, TypeAdapter

from app.models.venue import Venue
from app.repositories.json_venue_repository import JsonVenueRepository
from app.source_enrichment.website_passage_ingester import (
    WebsitePassage,
    WebsitePassageReport,
)

DEFAULT_MODEL_NAME = "BAAI/bge-small-en-v1.5"
DEFAULT_DENSE_WEIGHT = 0.9
RETRIEVAL_STOP_WORDS = {
    "a",
    "an",
    "and",
    "can",
    "for",
    "in",
    "my",
    "near",
    "of",
    "our",
    "place",
    "pub",
    "somewhere",
    "the",
    "to",
    "we",
    "where",
    "with",
}


class EvidenceChunk(BaseModel):
    """One embedding and its human-auditable supporting claim."""

    venueId: str
    text: str
    source: str
    sourceUrl: str | None = None
    attributes: list[str] = Field(default_factory=list)
    embedding: list[float]


class VenueRagIndex(BaseModel):
    """Portable local index metadata and vectors."""

    formatVersion: int = 1
    modelName: str
    venueDataSha256: str
    websitePassageDataSha256: str | None = None
    venueCount: int
    chunks: list[EvidenceChunk]


class RetrievedEvidence(BaseModel):
    """Evidence returned for one candidate venue."""

    semanticScore: float = Field(ge=0, le=1)
    texts: list[str]
    sources: list[str]
    sourceUrls: list[str]
    chunkIds: list[str] = Field(default_factory=list)


class EmbeddingProvider(Protocol):
    """Interface allowing local embedding implementations and deterministic tests."""

    modelName: str

    def embedPassages(self, passages: list[str]) -> list[list[float]]: ...

    def embedQuery(self, query: str) -> list[float]: ...


class EvidenceStore(Protocol):
    """Read only the evidence needed for the current candidate set."""

    modelName: str
    venueDataSha256: str
    backendName: str

    def chunksForVenues(self, venueIds: set[str]) -> list[EvidenceChunk]: ...

    def lexicalMatches(self, query: str, venueIds: set[str]) -> set[str]: ...


class FastEmbedProvider:
    """Generate retrieval-specific embeddings locally with FastEmbed."""

    def __init__(self, modelName: str = DEFAULT_MODEL_NAME, cachePath: Path | None = None) -> None:
        from fastembed import TextEmbedding

        self.modelName = modelName
        kwargs: dict[str, Any] = {"model_name": modelName}
        if cachePath is not None:
            kwargs["cache_dir"] = str(cachePath)
        self._model = TextEmbedding(**kwargs)

    def embedPassages(self, passages: list[str]) -> list[list[float]]:
        return [
            [float(value) for value in vector] for vector in self._model.passage_embed(passages)
        ]

    def embedQuery(self, query: str) -> list[float]:
        return [float(value) for value in next(iter(self._model.query_embed(query)))]


def fileSha256(path: Path) -> str:
    """Hash an input dataset so stale indexes cannot be used silently."""
    digest = hashlib.sha256()
    with path.open("rb") as inputFile:
        for block in iter(lambda: inputFile.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def filesSha256(paths: list[Path]) -> str:
    """Hash an ordered set of source files without creating a combined copy."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(fileSha256(path).encode("ascii"))
    return digest.hexdigest()


def _sourceFor(venue: Venue, attribute: str) -> tuple[str, str | None]:
    provenance = venue.attributeProvenance.get(attribute)
    if provenance is None:
        return venue.dataSource, venue.sourceUrl
    sourceUrl = (
        provenance.sourceRecordId
        if provenance.sourceRecordId.startswith(("http://", "https://"))
        else venue.sourceUrl
    )
    return provenance.source, sourceUrl


def _chunk(
    venue: Venue, text: str, attributes: list[str], sourceAttribute: str
) -> dict[str, object]:
    source, sourceUrl = _sourceFor(venue, sourceAttribute)
    return {
        "venueId": venue.venueId,
        "text": text,
        "source": source,
        "sourceUrl": sourceUrl,
        "attributes": attributes,
    }


def evidenceChunkId(chunk: EvidenceChunk) -> str:
    """Return a stable ID for Ragas evaluation without changing stored indexes."""
    identity = json.dumps(
        {
            "venueId": chunk.venueId,
            "text": chunk.text,
            "source": chunk.source,
            "sourceUrl": chunk.sourceUrl,
            "attributes": chunk.attributes,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def buildVenueChunks(venue: Venue) -> list[dict[str, object]]:
    """Turn validated venue fields into small, attributable retrieval passages."""
    identityParts = [f"{venue.name} is a pub in {venue.area}."]
    if venue.address or venue.postcode:
        address = ", ".join(value for value in (venue.address, venue.postcode) if value)
        identityParts.append(f"Its address is {address}.")
    if venue.tags:
        identityParts.append("It is described as " + ", ".join(sorted(venue.tags)) + ".")
    chunks = [
        _chunk(
            venue,
            " ".join(identityParts),
            ["name", "area", "address", "tags"],
            "name",
        )
    ]

    positiveFacts = (
        ("servesFood", venue.servesFood, f"{venue.name} serves food."),
        (
            "hasOutdoorSeating",
            venue.hasOutdoorSeating,
            f"{venue.name} has outdoor seating or a beer garden.",
        ),
        ("showsSports", venue.showsSports, f"{venue.name} shows live sport and football."),
        ("hasLiveMusic", venue.hasLiveMusic, f"{venue.name} hosts live music."),
        ("hasDj", venue.hasDj, f"{venue.name} hosts DJ events."),
        ("hostsEvents", venue.hostsEvents, f"{venue.name} hosts events."),
        ("dogFriendly", venue.dogFriendly, f"{venue.name} welcomes dogs and is dog friendly."),
        (
            "wheelchairAccessible",
            venue.wheelchairAccessible,
            f"{venue.name} has verified wheelchair access.",
        ),
        (
            "acceptsReservations",
            venue.acceptsReservations,
            f"{venue.name} accepts table bookings and reservations.",
        ),
        (
            "suitableForGroups",
            venue.suitableForGroups,
            f"{venue.name} supports groups, group bookings, or private hire.",
        ),
        ("hasHappyHour", venue.hasHappyHour, f"{venue.name} has a happy hour."),
        (
            "servesSundayRoast",
            venue.servesSundayRoast,
            f"{venue.name} serves a Sunday roast.",
        ),
        (
            "hasVeganOptions",
            venue.hasVeganOptions,
            f"{venue.name} has vegan or vegetarian food options.",
        ),
        ("hostsQuiz", venue.hostsQuiz, f"{venue.name} hosts a quiz night."),
        (
            "hasAccessibleToilet",
            venue.hasAccessibleToilet,
            f"{venue.name} has an accessible toilet.",
        ),
    )
    chunks.extend(
        _chunk(venue, text, [attribute], attribute)
        for attribute, value, text in positiveFacts
        if value is True
    )
    negativeFacts = (
        ("servesFood", venue.servesFood, f"{venue.name} does not serve food."),
        (
            "hasOutdoorSeating",
            venue.hasOutdoorSeating,
            f"{venue.name} does not have outdoor seating or a beer garden.",
        ),
        ("showsSports", venue.showsSports, f"{venue.name} does not show live sport."),
        ("hasLiveMusic", venue.hasLiveMusic, f"{venue.name} does not host live music."),
        ("hasDj", venue.hasDj, f"{venue.name} does not host DJ events."),
        ("hostsEvents", venue.hostsEvents, f"{venue.name} does not host events."),
        ("dogFriendly", venue.dogFriendly, f"{venue.name} does not allow dogs."),
        (
            "wheelchairAccessible",
            venue.wheelchairAccessible,
            f"{venue.name} does not have verified wheelchair access.",
        ),
        (
            "acceptsReservations",
            venue.acceptsReservations,
            f"{venue.name} does not accept table bookings or reservations.",
        ),
        (
            "suitableForGroups",
            venue.suitableForGroups,
            f"{venue.name} is not verified as suitable for groups or private hire.",
        ),
        ("hasHappyHour", venue.hasHappyHour, f"{venue.name} does not have a happy hour."),
        (
            "servesSundayRoast",
            venue.servesSundayRoast,
            f"{venue.name} does not serve a Sunday roast.",
        ),
        (
            "hasVeganOptions",
            venue.hasVeganOptions,
            f"{venue.name} does not have verified vegan or vegetarian options.",
        ),
        ("hostsQuiz", venue.hostsQuiz, f"{venue.name} does not host a quiz night."),
        (
            "hasAccessibleToilet",
            venue.hasAccessibleToilet,
            f"{venue.name} does not have an accessible toilet.",
        ),
    )
    chunks.extend(
        _chunk(venue, text, [attribute], attribute)
        for attribute, value, text in negativeFacts
        if value is False
    )
    if venue.noiseLevel is not None:
        chunks.append(
            _chunk(
                venue,
                f"{venue.name} has a {venue.noiseLevel} atmosphere.",
                ["noiseLevel"],
                "noiseLevel",
            )
        )
    if venue.priceLevel is not None:
        chunks.append(
            _chunk(
                venue,
                f"{venue.name} has a {venue.priceLevel} price level.",
                ["priceLevel"],
                "priceLevel",
            )
        )
    if venue.rating is not None:
        chunks.append(
            _chunk(
                venue,
                f"{venue.name} has a verified rating of {venue.rating:.1f} out of 5.",
                ["rating"],
                "rating",
            )
        )
    return chunks


def buildIndex(
    venues: list[Venue],
    venueDataPath: Path,
    provider: EmbeddingProvider,
    websitePassages: list[WebsitePassage] | None = None,
    websitePassageDataPath: Path | list[Path] | None = None,
) -> VenueRagIndex:
    """Embed grounded chunks and bind the index to its exact source dataset."""
    rawChunks = [chunk for venue in venues for chunk in buildVenueChunks(venue)]
    venueIds = {venue.venueId for venue in venues}
    rawChunks.extend(
        {
            "venueId": passage.venueId,
            "text": f"{passage.venueName}: {passage.text}",
            "source": passage.source,
            "sourceUrl": passage.pageUrl,
            "attributes": ["officialWebsiteText"],
        }
        for passage in websitePassages or []
        if passage.venueId in venueIds
    )
    embeddings = provider.embedPassages([str(chunk["text"]) for chunk in rawChunks])
    if len(embeddings) != len(rawChunks):
        raise ValueError("Embedding provider returned the wrong number of vectors")
    chunks = [
        EvidenceChunk.model_validate({**chunk, "embedding": embedding})
        for chunk, embedding in zip(rawChunks, embeddings, strict=True)
    ]
    return VenueRagIndex(
        modelName=provider.modelName,
        venueDataSha256=fileSha256(venueDataPath),
        websitePassageDataSha256=_websitePassageHash(websitePassageDataPath),
        venueCount=len(venues),
        chunks=chunks,
    )


def _websitePassageHash(paths: Path | list[Path] | None) -> str | None:
    if paths is None:
        return None
    if isinstance(paths, Path):
        return fileSha256(paths)
    return filesSha256(paths)


def writeIndex(
    index: VenueRagIndex, outputPath: Path, venues: list[Venue] | None = None
) -> None:
    """Write an index to SQLite by default, with legacy JSON support."""
    outputPath.parent.mkdir(parents=True, exist_ok=True)
    if outputPath.suffix in {".sqlite", ".sqlite3", ".db"}:
        _writeSqliteIndex(index, outputPath, venues)
        return
    outputPath.write_text(index.model_dump_json(indent=2) + "\n", encoding="utf-8")


def loadIndex(indexPath: Path, venueDataPath: Path) -> VenueRagIndex:
    """Load an index only when it matches the active venue dataset."""
    if indexPath.suffix in {".sqlite", ".sqlite3", ".db"}:
        index = _loadSqliteIndex(indexPath)
    else:
        index = TypeAdapter(VenueRagIndex).validate_json(indexPath.read_text(encoding="utf-8"))
    if index.venueDataSha256 != fileSha256(venueDataPath):
        raise ValueError("RAG index is stale: rebuild it for the active venue dataset")
    return index


def _writeSqliteIndex(
    index: VenueRagIndex, outputPath: Path, venues: list[Venue] | None = None
) -> None:
    """Store documents once and encode vectors as compact binary values."""
    with sqlite3.connect(outputPath) as connection:
        connection.executescript(
            """
            DROP TABLE IF EXISTS metadata;
            DROP TABLE IF EXISTS evidence_chunks_fts;
            DROP TABLE IF EXISTS evidence_chunks;
            DROP TABLE IF EXISTS venues;
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE evidence_chunks (
                chunk_id TEXT PRIMARY KEY,
                venue_id TEXT NOT NULL,
                text TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT,
                attributes_json TEXT NOT NULL,
                embedding BLOB NOT NULL,
                embedding_dimensions INTEGER NOT NULL
            );
            CREATE INDEX evidence_chunks_venue_id_idx ON evidence_chunks(venue_id);
            CREATE TABLE venues (
                venue_id TEXT PRIMARY KEY,
                name_key TEXT NOT NULL,
                area TEXT NOT NULL,
                latitude REAL NOT NULL,
                longitude REAL NOT NULL,
                venue_json TEXT NOT NULL
            );
            CREATE INDEX venues_area_idx ON venues(area);
            CREATE VIRTUAL TABLE evidence_chunks_fts USING fts5(
                chunk_id UNINDEXED,
                venue_id UNINDEXED,
                text,
                attributes,
                tokenize='porter unicode61'
            );
            """
        )
        metadata = {
            "formatVersion": str(index.formatVersion),
            "modelName": index.modelName,
            "venueDataSha256": index.venueDataSha256,
            "websitePassageDataSha256": index.websitePassageDataSha256 or "",
            "venueCount": str(index.venueCount),
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items()
        )
        connection.executemany(
            """
            INSERT INTO venues(
                venue_id, name_key, area, latitude, longitude, venue_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    venue.venueId,
                    venue.name.casefold(),
                    venue.area,
                    venue.latitude,
                    venue.longitude,
                    venue.model_dump_json(),
                )
                for venue in venues or []
            ),
        )
        connection.executemany(
            """
            INSERT INTO evidence_chunks(
                chunk_id, venue_id, text, source, source_url,
                attributes_json, embedding, embedding_dimensions
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    evidenceChunkId(chunk),
                    chunk.venueId,
                    chunk.text,
                    chunk.source,
                    chunk.sourceUrl,
                    json.dumps(chunk.attributes, separators=(",", ":")),
                    # Preserve the existing ranking exactly. Float64 is still
                    # far smaller than decimal vectors repeated in JSON.
                    struct.pack(f"<{len(chunk.embedding)}d", *chunk.embedding),
                    len(chunk.embedding),
                )
                for chunk in index.chunks
            ),
        )
        connection.execute(
            """
            INSERT INTO evidence_chunks_fts(chunk_id, venue_id, text, attributes)
            SELECT chunk_id, venue_id, text, attributes_json FROM evidence_chunks
            """
        )


def _loadSqliteIndex(indexPath: Path) -> VenueRagIndex:
    """Load the portable in-memory view used by the current small retriever."""
    with sqlite3.connect(indexPath) as connection:
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        rows = connection.execute(
            """
            SELECT venue_id, text, source, source_url, attributes_json,
                   embedding, embedding_dimensions
            FROM evidence_chunks
            ORDER BY rowid
            """
        ).fetchall()
    chunks = [
        EvidenceChunk(
            venueId=str(row[0]),
            text=str(row[1]),
            source=str(row[2]),
            sourceUrl=str(row[3]) if row[3] is not None else None,
            attributes=json.loads(str(row[4])),
            embedding=list(struct.unpack(f"<{int(row[6])}d", bytes(row[5]))),
        )
        for row in rows
    ]
    return VenueRagIndex(
        formatVersion=int(metadata["formatVersion"]),
        modelName=metadata["modelName"],
        venueDataSha256=metadata["venueDataSha256"],
        websitePassageDataSha256=metadata["websitePassageDataSha256"] or None,
        venueCount=int(metadata["venueCount"]),
        chunks=chunks,
    )


class SqliteEvidenceStore:
    """Database-native evidence access with candidate filtering and FTS5."""

    backendName = "sqlite_fts5"

    def __init__(
        self, databasePath: Path, venueDataPath: Path, verifyVenueData: bool = True
    ) -> None:
        self.databasePath = databasePath
        with sqlite3.connect(databasePath) as connection:
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            hasFts = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'evidence_chunks_fts'"
            ).fetchone()
        if verifyVenueData and metadata["venueDataSha256"] != fileSha256(venueDataPath):
            raise ValueError("RAG evidence database is stale: rebuild it for the active dataset")
        if hasFts is None:
            raise ValueError("RAG evidence database lacks FTS5 data: rebuild it")
        self.modelName = metadata["modelName"]
        self.venueDataSha256 = metadata["venueDataSha256"]
        self.venueCount = int(metadata["venueCount"])

    def chunksForVenues(self, venueIds: set[str]) -> list[EvidenceChunk]:
        """Load only documents belonging to the filtered candidate venues."""
        if not venueIds:
            return []
        rows: list[tuple[object, ...]] = []
        orderedIds = sorted(venueIds)
        # Keep below SQLite's common 999-parameter limit.
        with sqlite3.connect(self.databasePath) as connection:
            for start in range(0, len(orderedIds), 900):
                batch = orderedIds[start : start + 900]
                placeholders = ",".join("?" for _ in batch)
                rows.extend(
                    connection.execute(
                        f"""
                        SELECT venue_id, text, source, source_url, attributes_json,
                               embedding, embedding_dimensions
                        FROM evidence_chunks
                        WHERE venue_id IN ({placeholders})
                        ORDER BY venue_id, rowid
                        """,  # noqa: S608 - placeholders contain no user data
                        batch,
                    ).fetchall()
                )
        return [_chunkFromSqliteRow(row) for row in rows]

    def lexicalMatches(self, query: str, venueIds: set[str]) -> set[str]:
        """Return exact FTS5 matches limited to current candidates."""
        terms = sorted(_tokens(query))
        if not terms or not venueIds:
            return set()
        ftsQuery = " OR ".join(f'"{term}"' for term in terms)
        matches: set[str] = set()
        orderedIds = sorted(venueIds)
        with sqlite3.connect(self.databasePath) as connection:
            for start in range(0, len(orderedIds), 900):
                batch = orderedIds[start : start + 900]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"""
                    SELECT chunk_id
                    FROM evidence_chunks_fts
                    WHERE evidence_chunks_fts MATCH ?
                      AND venue_id IN ({placeholders})
                    """,  # noqa: S608 - placeholders contain no user data
                    [ftsQuery, *batch],
                )
                matches.update(str(row[0]) for row in rows)
        return matches


def _chunkFromSqliteRow(row: tuple[object, ...]) -> EvidenceChunk:
    embeddingDimensions = cast(int, row[6])
    embeddingBytes = cast(bytes, row[5])
    return EvidenceChunk(
        venueId=str(row[0]),
        text=str(row[1]),
        source=str(row[2]),
        sourceUrl=str(row[3]) if row[3] is not None else None,
        attributes=json.loads(str(row[4])),
        embedding=list(struct.unpack(f"<{embeddingDimensions}d", embeddingBytes)),
    )


def loadEvidenceStore(
    indexPath: Path, venueDataPath: Path, verifyVenueData: bool = True
) -> EvidenceStore:
    """Prefer lazy SQLite access while retaining legacy JSON compatibility."""
    if indexPath.suffix in {".sqlite", ".sqlite3", ".db"}:
        return SqliteEvidenceStore(indexPath, venueDataPath, verifyVenueData)
    return InMemoryEvidenceStore(loadIndex(indexPath, venueDataPath))


class InMemoryEvidenceStore:
    """Compatibility adapter for tests and legacy JSON indexes."""

    backendName = "memory"

    def __init__(self, index: VenueRagIndex) -> None:
        self.index = index
        self.modelName = index.modelName
        self.venueDataSha256 = index.venueDataSha256
        self.venueCount = index.venueCount

    def chunksForVenues(self, venueIds: set[str]) -> list[EvidenceChunk]:
        return [chunk for chunk in self.index.chunks if chunk.venueId in venueIds]

    def lexicalMatches(self, query: str, venueIds: set[str]) -> set[str]:
        queryTokens = _tokens(query)
        return {
            evidenceChunkId(chunk)
            for chunk in self.chunksForVenues(venueIds)
            if queryTokens.intersection(_tokens(chunk.text))
        }


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("Query and passage embeddings have different dimensions")
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.casefold())
        if token not in RETRIEVAL_STOP_WORDS and len(token) > 1
    }


def _hybridScore(
    queryTokens: set[str], chunk: EvidenceChunk, denseScore: float, denseWeight: float
) -> float:
    lexicalScore = len(queryTokens & _tokens(chunk.text)) / len(queryTokens) if queryTokens else 0.0
    return denseWeight * max(0.0, min(1.0, denseScore)) + (1.0 - denseWeight) * lexicalScore


class HybridRagRetriever:
    """Retrieve the strongest grounded chunks for already-filtered candidates."""

    def __init__(
        self,
        index: VenueRagIndex | EvidenceStore,
        provider: EmbeddingProvider,
        denseWeight: float = DEFAULT_DENSE_WEIGHT,
    ) -> None:
        store = InMemoryEvidenceStore(index) if isinstance(index, VenueRagIndex) else index
        if store.modelName != provider.modelName:
            raise ValueError("RAG index and query embedding models do not match")
        if not 0.0 <= denseWeight <= 1.0:
            raise ValueError("denseWeight must be between 0 and 1")
        self.store = store
        self.provider = provider
        self.denseWeight = denseWeight

    @property
    def backendName(self) -> str:
        """Expose the active evidence backend for readiness diagnostics."""
        return self.store.backendName

    def retrieve(
        self,
        query: str,
        candidateVenueIds: Iterable[str],
        evidenceLimit: int = 3,
        preferredAttributes: set[str] | None = None,
    ) -> dict[str, RetrievedEvidence]:
        candidateIds = set(candidateVenueIds)
        queryEmbedding = self.provider.embedQuery(query)
        queryTokens = _tokens(query)
        ftsMatches = self.store.lexicalMatches(query, candidateIds)
        scored: dict[str, list[tuple[float, float, EvidenceChunk]]] = {}
        for chunk in self.store.chunksForVenues(candidateIds):
            denseScore = _cosine(queryEmbedding, chunk.embedding)
            hybridScore = _hybridScore(queryTokens, chunk, denseScore, self.denseWeight)
            if evidenceChunkId(chunk) in ftsMatches:
                hybridScore = min(1.0, hybridScore + (1.0 - self.denseWeight) * 0.25)
            scored.setdefault(chunk.venueId, []).append(
                (
                    hybridScore,
                    denseScore,
                    chunk,
                )
            )
        results: dict[str, RetrievedEvidence] = {}
        for venueId, venueChunks in scored.items():
            rankedChunks = sorted(venueChunks, key=lambda item: item[0], reverse=True)
            # For structured requests, explanations should lead with the exact
            # verified facts requested by the user, not generic address text.
            attributeChunks = [
                item
                for item in rankedChunks
                if preferredAttributes and preferredAttributes.intersection(item[2].attributes)
            ]
            topChunks = (attributeChunks or rankedChunks)[:evidenceLimit]
            scoreChunks = rankedChunks[:evidenceLimit]
            # Combine terms across selected evidence so conjunctions can span passages.
            evidenceTokens = set().union(*(_tokens(chunk.text) for _, _, chunk in scoreChunks))
            lexicalCoverage = (
                len(queryTokens & evidenceTokens) / len(queryTokens) if queryTokens else 0.0
            )
            strongestDenseScore = max(denseScore for _, denseScore, _ in scoreChunks)
            semanticScore = (
                self.denseWeight * max(0.0, min(1.0, strongestDenseScore))
                + (1.0 - self.denseWeight) * lexicalCoverage
            )
            results[venueId] = RetrievedEvidence(
                semanticScore=round(semanticScore, 4),
                texts=[chunk.text for _, _, chunk in topChunks],
                sources=list(dict.fromkeys(chunk.source for _, _, chunk in topChunks)),
                sourceUrls=list(
                    dict.fromkeys(
                        chunk.sourceUrl for _, _, chunk in topChunks if chunk.sourceUrl is not None
                    )
                ),
                chunkIds=[evidenceChunkId(chunk) for _, _, chunk in topChunks],
            )
        return results

    def referenceChunks(self, venueIds: set[str]) -> list[EvidenceChunk]:
        """Expose bounded reference documents to the offline evaluator."""
        return self.store.chunksForVenues(venueIds)


def buildArgumentParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("venues", type=Path, help="Validated venue JSON")
    parser.add_argument("output", type=Path, help="Local RAG index JSON")
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--cache", type=Path, default=Path("data/local/models"))
    parser.add_argument(
        "--website-passages",
        type=Path,
        nargs="+",
        help="One or more passage reports produced by website_passage_ingester",
    )
    return parser


def main() -> None:
    args = buildArgumentParser().parse_args()
    venues = JsonVenueRepository(args.venues).listVenues()
    provider = FastEmbedProvider(args.model, args.cache)
    websitePassages: list[WebsitePassage] = []
    for passagePath in args.website_passages or []:
        passageReport = TypeAdapter(WebsitePassageReport).validate_json(
            passagePath.read_text(encoding="utf-8")
        )
        websitePassages.extend(passageReport.passages)
    index = buildIndex(
        venues,
        args.venues,
        provider,
        websitePassages,
        args.website_passages,
    )
    writeIndex(index, args.output, venues)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "model": index.modelName,
                "venues": index.venueCount,
                "chunks": len(index.chunks),
                "websitePassages": len(websitePassages),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
