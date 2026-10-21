"""Project folders and file names, resolved from the checkout rather than from the cwd.

Every entry point takes ``--config``; these are only its defaults. ``config.json`` sits in
``src/`` beside the package, while ``assets/`` and ``logs/`` stay beside ``pyproject.toml``,
so they are found through ``PROJECT_ROOT`` instead of the current directory.

This assumes an editable install (``uv sync``'s default): ``__file__`` is the real file
in the checkout. A non-editable wheel would point at ``site-packages`` instead, which
this bench never installs.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # discovery/dobot, the uv project
CONFIG_PATH = PROJECT_ROOT / "src" / "config.json"
ASSETS_DIR = PROJECT_ROOT / "assets"
LOGS_DIR = PROJECT_ROOT / "logs"


def from_root(path: str | Path) -> Path:
    """Resolve a relative path from ``config.json`` against the project directory.

    ``homography_path`` and ``log_dir`` are stored relative (``assets/homography.json``,
    ``logs``) so the file stays portable between benches; an absolute value is kept.
    """
    path = Path(path)
    return path if path.is_absolute() else PROJECT_ROOT / path
