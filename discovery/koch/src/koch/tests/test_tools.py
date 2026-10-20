"""Every bench tool is a module: importing it opens no port, and ``--help`` answers."""

import subprocess
import sys

import pytest

TOOLS = [
    "fit_twin",
    "match_leader",
    "pos_trace",
    "preflight",
    "reg_diff",
    "register_snapshot",
    "spawn_detached",
    "torque_off",
    "watchdog",
]


@pytest.mark.integration
@pytest.mark.parametrize("name", TOOLS)
def test_importing_a_tool_has_no_side_effects(name: str) -> None:
    subprocess.run(
        [sys.executable, "-c", f"import koch.tools.{name}"],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.integration
@pytest.mark.parametrize("name", TOOLS)
def test_every_tool_answers_help(name: str) -> None:
    out = subprocess.run(
        [sys.executable, "-m", f"koch.tools.{name}", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "usage" in out.stdout.lower()
