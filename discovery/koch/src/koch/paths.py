"""Project folders and file names, resolved from the checkout rather than from ``src/``.

Every entry point takes ``--config-dir`` / ``--work-dir``; these are only their defaults.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # discovery/koch, the uv project
CONFIG_DIR = ROOT / "config"
WORK_DIR = ROOT / "work"
WEBXR_DIR = ROOT / "webxr"
CONFIG_NAME = "config.json"
TWIN_CONFIG_NAME = "twin.json"


def calibration_dir(
    config_dir: Path, kind: str, *, use_lerobot_cache: bool
) -> Path | None:
    """Where lerobot reads and writes the calibration of one ``kind`` of arm.

    ``kind`` is ``koch_leader`` or ``koch_follower``. ``None`` tells lerobot to keep
    its own cache (``~/.cache/huggingface/lerobot/calibration``) instead.
    """
    if use_lerobot_cache:
        return None
    return Path(config_dir) / "calibration" / kind
