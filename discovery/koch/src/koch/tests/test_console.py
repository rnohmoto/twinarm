"""Console hygiene shared by the entry points: the clamp-warning filter and stdout."""

import logging

import pytest

from koch import console


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        "lerobot", logging.WARNING, __file__, 1, message, None, None
    )


@pytest.mark.unit
def test_filter_drops_the_per_frame_clamp_warning_and_keeps_the_rest() -> None:
    flt = console.DropClampWarning()
    assert not flt.filter(_record("Relative goal clamped to be safe: gripper 40 -> 20"))
    assert flt.filter(_record("Device disconnected"))


@pytest.mark.unit
def test_install_clamp_filter_is_idempotent() -> None:
    logger = logging.getLogger("koch.tests.console")
    console.install_clamp_filter(logger)
    console.install_clamp_filter(logger)
    assert sum(isinstance(f, console.DropClampWarning) for f in logger.filters) == 1


@pytest.mark.unit
def test_reconfigure_stdout_never_raises() -> None:
    console.reconfigure_stdout()
