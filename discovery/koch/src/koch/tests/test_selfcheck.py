"""``koch-teleop --selftest`` must keep working on a field machine without pytest."""

import pytest

from koch import selfcheck


@pytest.mark.unit
def test_run_passes_and_reports(capsys: pytest.CaptureFixture[str]) -> None:
    selfcheck.run()
    assert "selftest OK" in capsys.readouterr().out
