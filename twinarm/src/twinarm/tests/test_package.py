"""Smoke test confirming the package is installed and importable."""

import pytest

import twinarm


@pytest.mark.unit
def test_package_exposes_version() -> None:
    assert isinstance(twinarm.__version__, str)
    assert twinarm.__version__
