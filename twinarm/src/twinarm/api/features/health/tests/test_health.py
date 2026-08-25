"""Tests for the health slice."""

import pytest
from fastapi.testclient import TestClient

import twinarm
from twinarm.api import create_app


@pytest.mark.unit
def test_health_reports_ok_and_package_version() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": twinarm.__version__}
