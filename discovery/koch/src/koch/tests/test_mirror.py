"""``koch.feedback`` and ``koch.link`` are verbatim mirrors of ``twinarm.domain``.

This sandbox cannot import the library (separate uv projects), so the copies are
compared as syntax trees: every class and function of the mirror must match the
original one by one, docstrings aside. Skipped when the library is not checked
out beside this project.
"""

import ast
from pathlib import Path

import pytest

from koch import paths

DOMAIN = paths.ROOT.parents[1] / "twinarm" / "src" / "twinarm" / "domain"
PAIRS = [
    ("feedback.py", "gripper_feedback.py"),
    ("link.py", "link_watchdog.py"),
]


def _definitions(source: Path) -> dict[str, str]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            _strip_docstring(node)
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, ast.FunctionDef):
                        _strip_docstring(child)
            out[node.name] = ast.dump(node)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                out[target.id] = ast.dump(node.value)
    return out


def _strip_docstring(node: ast.FunctionDef | ast.ClassDef) -> None:
    first = node.body[0] if node.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        node.body = node.body[1:] or [ast.Pass()]


@pytest.mark.unit
@pytest.mark.parametrize(("mirror", "original"), PAIRS)
def test_mirror_matches_the_library_definition_by_definition(
    mirror: str, original: str
) -> None:
    source = DOMAIN / original
    if not source.is_file():
        pytest.skip(f"{source} is not checked out beside this project")
    ours = _definitions(Path(paths.ROOT, "src", "koch", mirror))
    theirs = _definitions(source)
    assert set(theirs) <= set(ours), (
        f"missing in {mirror}: {sorted(set(theirs) - set(ours))}"
    )
    different = [name for name in theirs if ours[name] != theirs[name]]
    assert different == [], f"{mirror} differs from {original} in: {different}"
