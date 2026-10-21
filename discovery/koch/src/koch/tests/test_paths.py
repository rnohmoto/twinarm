"""``koch.paths`` resolves the project folders from the checkout, not from ``src/``."""

from pathlib import Path

import pytest

from koch import paths


@pytest.mark.unit
def test_root_is_the_project_checkout_not_the_package() -> None:
    assert paths.ROOT.is_absolute()
    assert paths.ROOT.name == "koch"
    assert "src" not in paths.ROOT.parts
    assert (paths.ROOT / "pyproject.toml").is_file()


@pytest.mark.unit
def test_default_folders_hang_off_root() -> None:
    assert paths.CONFIG_DIR == paths.ROOT / "config"
    assert paths.WORK_DIR == paths.ROOT / "work"
    assert paths.WEBXR_DIR == paths.ROOT / "src" / "webxr"


@pytest.mark.unit
def test_file_names_are_the_renamed_ones() -> None:
    assert paths.CONFIG_NAME == "config.json"
    assert paths.TWIN_CONFIG_NAME == "twin.json"


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["koch_leader", "koch_follower"])
def test_calibration_dir_is_under_config_unless_the_lerobot_cache_is_wanted(
    tmp_path: Path, kind: str
) -> None:
    expected = tmp_path / "calibration" / kind
    assert paths.calibration_dir(tmp_path, kind, use_lerobot_cache=False) == expected
    assert paths.calibration_dir(tmp_path, kind, use_lerobot_cache=True) is None
