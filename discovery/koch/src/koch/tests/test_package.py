"""Smoke test confirming the package is installed and importable."""

import pytest

import koch


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert isinstance(koch.__version__, str)
    assert koch.__version__
