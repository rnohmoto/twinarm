"""Console hygiene for the entry points, applied at the top of ``main()``, never at import.

Importing a ``koch`` module must leave the process untouched (tests import them);
the two things the scripts used to do at import time live here as explicit calls.
"""

import logging
import sys

CLAMP_WARNING = "clamped to be safe"


class DropClampWarning(logging.Filter):
    """Silence lerobot's per-frame clamp warning (harmless, caused by pose gaps).

    While the gripper is blocked (a hand, an object) the goal stays beyond
    ``max_relative_target`` and lerobot would log the clamp every frame. lerobot
    emits it through the root logger, so a filter on the root logger is what works.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Drop records that mention the safety clamp."""
        return CLAMP_WARNING not in str(record.getMessage())


def install_clamp_filter(logger: logging.Logger | None = None) -> None:
    """Attach the clamp filter once to ``logger`` (the root logger by default)."""
    target = logging.getLogger() if logger is None else logger
    if not any(isinstance(f, DropClampWarning) for f in target.filters):
        target.addFilter(DropClampWarning())


def reconfigure_stdout() -> None:
    """Never crash on a symbol the console cannot encode (Windows cp932)."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(errors="replace")
