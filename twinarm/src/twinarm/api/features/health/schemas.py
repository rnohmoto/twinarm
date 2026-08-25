"""Schemas owned by the health slice."""

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    """Liveness report returned by ``GET /health``."""

    model_config = ConfigDict(frozen=True)

    status: str
    version: str
