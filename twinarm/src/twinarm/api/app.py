"""Application factory for the TwinArm API."""

from fastapi import FastAPI

from twinarm import __version__
from twinarm.api.features.health.router import router as health_router


def create_app() -> FastAPI:
    """Assemble the FastAPI application from the feature slices.

    Returns
    -------
    FastAPI
        The application instance. Building it must not touch hardware.
    """
    app = FastAPI(title="twinarm", version=__version__)
    app.include_router(health_router)
    return app
