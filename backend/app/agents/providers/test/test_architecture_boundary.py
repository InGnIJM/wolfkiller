import ast
import sys
from pathlib import Path


APP = Path(__file__).resolve().parents[3]


def imports_matching(roots: list[Path], forbidden_modules: str | tuple[str, ...]) -> list[tuple[str, str]]:
    """Return source files and imports that cross a forbidden architecture boundary."""
    forbidden = (forbidden_modules,) if isinstance(forbidden_modules, str) else forbidden_modules
    offenders: list[tuple[str, str]] = []

    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules = (
                    [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module]
                    if isinstance(node, ast.ImportFrom) and node.module is not None
                    else []
                )
                for module in modules:
                    if any(module == forbidden_module or module.startswith(f"{forbidden_module}.")
                           for forbidden_module in forbidden):
                        offenders.append((path.relative_to(APP).as_posix(), module))

    return sorted(offenders)


def test_imports_matching_reports_import_and_from_import(tmp_path: Path, monkeypatch) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "boundary_violation.py").write_text(
        "import app.roles.guard\nfrom app.core.scheduler import Scheduler\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "APP", tmp_path)

    assert imports_matching([source_root], ("app.core", "app.roles")) == [
        ("source/boundary_violation.py", "app.core.scheduler"),
        ("source/boundary_violation.py", "app.roles.guard"),
    ]


def test_game_core_and_roles_do_not_import_provider_layer() -> None:
    offenders = imports_matching([APP / "core", APP / "roles"], "app.agents.providers")

    assert offenders == [], f"Game core and roles must not import provider modules: {offenders}"


def test_provider_layer_does_not_import_game_core_or_roles() -> None:
    offenders = imports_matching([APP / "agents" / "providers"], ("app.core", "app.roles"))

    assert offenders == [], f"Provider modules must not import game core or roles: {offenders}"
