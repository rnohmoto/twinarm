"""Routes owned by the health slice."""

from fastapi import APIRouter

from twinarm import __version__

from .schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health")
async def get_health() -> HealthResponse:
    """Report service liveness and the installed package version."""
    return HealthResponse(status="ok", version=__version__)
