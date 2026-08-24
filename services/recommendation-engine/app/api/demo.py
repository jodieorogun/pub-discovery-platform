"""Local browser demo for recommendations and feedback."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["demo"])
ASSET_DIRECTORY = Path(__file__).resolve().parents[1] / "web_assets"


@router.get("/", include_in_schema=False, response_class=FileResponse)
def getDemo() -> FileResponse:
    """Serve the local recommendation interface."""
    return FileResponse(ASSET_DIRECTORY / "index.html", media_type="text/html")


@router.get("/assets/app.css", include_in_schema=False, response_class=FileResponse)
def getDemoStyles() -> FileResponse:
    """Serve the demo stylesheet."""
    return FileResponse(ASSET_DIRECTORY / "app.css", media_type="text/css")


@router.get("/assets/app.js", include_in_schema=False, response_class=FileResponse)
def getDemoScript() -> FileResponse:
    """Serve the demo JavaScript."""
    return FileResponse(ASSET_DIRECTORY / "app.js", media_type="text/javascript")


@router.get("/review", include_in_schema=False, response_class=FileResponse)
def getFeatureReviewPage() -> FileResponse:
    """Serve the local human-review queue interface."""
    return FileResponse(ASSET_DIRECTORY / "review.html", media_type="text/html")


@router.get("/assets/review.js", include_in_schema=False, response_class=FileResponse)
def getFeatureReviewScript() -> FileResponse:
    """Serve the local review queue behavior."""
    return FileResponse(ASSET_DIRECTORY / "review.js", media_type="text/javascript")


@router.get("/diary", include_in_schema=False, response_class=FileResponse)
def getDiaryPage() -> FileResponse:
    """Serve the signed-in user's private pub diary."""
    return FileResponse(ASSET_DIRECTORY / "diary.html", media_type="text/html")


@router.get("/assets/diary.js", include_in_schema=False, response_class=FileResponse)
def getDiaryScript() -> FileResponse:
    """Serve private diary interactions."""
    return FileResponse(ASSET_DIRECTORY / "diary.js", media_type="text/javascript")
