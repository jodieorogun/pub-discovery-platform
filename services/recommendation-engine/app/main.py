"""FastAPI application entry point."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status

from app.api.accounts import router as accountsRouter
from app.api.demo import router as demoRouter
from app.api.feature_reviews import router as featureReviewsRouter
from app.api.feedback import router as feedbackRouter
from app.api.price_observations import router as priceObservationsRouter
from app.api.recommendations import router as recommendationsRouter
from app.parsing.ollama_intent_parser import DEFAULT_OLLAMA_URL, IntentParser, OllamaIntentParser
from app.parsing.semantic_intent_parser import SemanticIntentParser
from app.rag.venue_index import FastEmbedProvider, HybridRagRetriever, loadEvidenceStore
from app.repositories.json_venue_repository import JsonVenueRepository
from app.repositories.price_enriched_venue_repository import PriceEnrichedVenueRepository
from app.repositories.sqlite_account_repository import SqliteAccountRepository
from app.repositories.sqlite_feature_review_repository import SqliteFeatureReviewRepository
from app.repositories.sqlite_feedback_repository import SqliteFeedbackRepository
from app.repositories.sqlite_price_observation_repository import SqlitePriceObservationRepository
from app.repositories.sqlite_venue_repository import SqliteVenueRepository
from app.services.account_service import AccountService
from app.services.feedback_service import FeedbackService
from app.services.recommendation_service import RecommendationService


def _fileVersion(path: Path) -> str:
    """Return a short content hash suitable for API diagnostics."""
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()[:12]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load immutable venue data once when the application starts."""
    defaultDataPath = Path(__file__).resolve().parents[1] / "data" / "sample_venues.json"
    dataPath = Path(os.environ.get("VENUE_DATA_PATH", defaultDataPath))
    databaseValue = os.environ.get("APP_DATABASE_PATH") or os.environ.get("RAG_INDEX_PATH")
    databasePath = Path(databaseValue) if databaseValue else None
    baseVenueRepository = (
        SqliteVenueRepository(databasePath)
        if databasePath is not None
        else JsonVenueRepository(dataPath)
    )
    defaultPricePath = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "local"
        / "price_observations.sqlite3"
    )
    pricePath = Path(os.environ.get("PRICE_DB_PATH", str(defaultPricePath)))
    priceObservations = SqlitePriceObservationRepository(pricePath)
    venueRepository = PriceEnrichedVenueRepository(baseVenueRepository, priceObservations)
    datasetVersion = _fileVersion(dataPath) if databasePath is None else "database"
    ragRetriever = None
    intentParser: IntentParser | None = None
    ollamaParser = None
    indexVersion = None
    ragIndexValue = os.environ.get("RAG_INDEX_PATH") or os.environ.get("APP_DATABASE_PATH")
    if ragIndexValue:
        indexPath = Path(ragIndexValue)
        # The database metadata is already hash-bound to the JSON export used
        # at build time; runtime venue rows now come from that same database.
        evidenceStore = loadEvidenceStore(indexPath, dataPath, verifyVenueData=databasePath is None)
        datasetVersion = evidenceStore.venueDataSha256[:12]
        indexVersion = _fileVersion(indexPath)
        cachePathValue = os.environ.get("RAG_MODEL_CACHE_PATH")
        provider = FastEmbedProvider(
            evidenceStore.modelName,
            Path(cachePathValue) if cachePathValue else None,
        )
        ragRetriever = HybridRagRetriever(evidenceStore, provider)
        intentParser = SemanticIntentParser(provider)
        ollamaModel = os.environ.get("OLLAMA_MODEL")
        if ollamaModel:
            ollamaParser = OllamaIntentParser(
                model=ollamaModel,
                fallback=intentParser,
                baseUrl=os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL),
                timeoutSeconds=float(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "8")),
            )
            intentParser = ollamaParser
    app.state.recommendationService = RecommendationService(
        venueRepository,
        ragRetriever,
        intentParser,
        datasetVersion=datasetVersion,
        indexVersion=indexVersion,
    )
    app.state.readiness = {
        "status": "ready",
        "venueCount": len(venueRepository.listVenues()),
        "datasetVersion": datasetVersion,
        "indexVersion": indexVersion,
        "ragEnabled": ragRetriever is not None,
        "evidenceBackend": ragRetriever.backendName if ragRetriever is not None else None,
        "localLlmConfigured": ollamaParser is not None,
    }
    defaultFeedbackPath = (
        Path(__file__).resolve().parents[1] / "data" / "local" / "feedback.sqlite3"
    )
    feedbackPath = Path(
        os.environ.get("FEEDBACK_DB_PATH", str(databasePath or defaultFeedbackPath))
    )
    feedbackRepository = SqliteFeedbackRepository(feedbackPath)
    app.state.feedbackService = FeedbackService(feedbackRepository, venueRepository)
    # Personal data must survive venue/evidence database rebuilds.
    defaultAccountPath = (
        Path(__file__).resolve().parents[1] / "data" / "local" / "accounts.sqlite3"
    )
    accountPath = Path(os.environ.get("ACCOUNT_DB_PATH", str(defaultAccountPath)))
    accountRepository = SqliteAccountRepository(accountPath)
    app.state.accountService = AccountService(accountRepository, venueRepository)
    if os.environ.get("DEMO_ADMIN_ENABLED", "true").lower() == "true":
        app.state.accountService.ensureDemoAdmin(
            os.environ.get("DEMO_ADMIN_EMAIL", "admin@admin.com"),
            os.environ.get("DEMO_ADMIN_PASSWORD", "admin"),
        )
    app.state.featureReviewRepository = SqliteFeatureReviewRepository(feedbackPath)
    app.state.priceObservationRepository = priceObservations
    app.state.venueRepository = venueRepository
    try:
        yield
    finally:
        if ollamaParser is not None:
            ollamaParser.close()


app = FastAPI(
    title="AI Recommendation Engine",
    description="Deterministic venue recommendations with transparent ranking.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(recommendationsRouter)
app.include_router(accountsRouter)
app.include_router(feedbackRouter)
app.include_router(featureReviewsRouter)
app.include_router(priceObservationsRouter)
app.include_router(demoRouter)


@app.get("/health", tags=["health"])
def getHealth() -> dict[str, str]:
    """Return a lightweight service health check."""
    return {"status": "healthy"}


@app.get("/ready", tags=["health"])
def getReadiness(request: Request) -> dict[str, str | int | bool | None]:
    """Report whether validated data and optional retrieval state loaded successfully."""
    readiness = getattr(request.app.state, "readiness", None)
    if readiness is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recommendation data is not ready",
        )
    return readiness  # type: ignore[no-any-return]
