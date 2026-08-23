import ast
import sys
from pathlib import Path


APP = Path(__file__).resolve().parents[3]


def is_forbidden(module: str, forbidden_modules: tuple[str, ...]) -> bool:
    return any(
        module == forbidden_module or module.startswith(f"{forbidden_module}.")
        for forbidden_module in forbidden_modules
    )


def resolve_import_from_base(path: Path, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module

    package_parts = (APP.name, *path.parent.relative_to(APP).parts)
    if node.level > len(package_parts):
        return None
    base_parts = package_parts[:len(package_parts) - node.level + 1]
    if node.module is not None:
        base_parts += tuple(node.module.split("."))
    return ".".join(base_parts)


def matching_import_from_modules(
    path: Path, node: ast.ImportFrom, forbidden_modules: tuple[str, ...],
) -> list[str]:
    base = resolve_import_from_base(path, node)
    if base is None:
        return []
    if is_forbidden(base, forbidden_modules):
        return [base]
    return [
        candidate
        for alias in node.names
        if alias.name != "*"
        if is_forbidden(candidate := f"{base}.{alias.name}", forbidden_modules)
    ]


def imports_matching(roots: list[Path], forbidden_modules: str | tuple[str, ...]) -> list[tuple[str, str]]:
    """Return source files and imports that cross a forbidden architecture boundary."""
    forbidden = (forbidden_modules,) if isinstance(forbidden_modules, str) else forbidden_modules
    offenders: list[tuple[str, str]] = []

    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(
                        alias.name for alias in node.names
                        if is_forbidden(alias.name, forbidden)
                    )
                elif isinstance(node, ast.ImportFrom):
                    modules.extend(matching_import_from_modules(path, node, forbidden))
                for module in modules:
                    offenders.append((path.relative_to(APP).as_posix(), module))

    return sorted(offenders)


def test_imports_matching_resolves_import_from_aliases_and_relative_levels(
    tmp_path: Path, monkeypatch,
) -> None:
    app_root = tmp_path / "app"
    source_root = app_root / "agents" / "providers"
    source_root.mkdir(parents=True)
    (source_root / "boundary_violation.py").write_text(
        "\n".join((
            "import app.roles.guard",
            "from app.core.scheduler import Scheduler",
            "from app import core",
            "from app.agents import providers",
            "from ... import core",
            "from ...core import Scheduler",
            "from .... import core",
            "from app import *",
        )),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "APP", app_root)

    assert imports_matching([source_root], ("app.core", "app.roles", "app.agents.providers")) == [
        ("agents/providers/boundary_violation.py", "app.agents.providers"),
        ("agents/providers/boundary_violation.py", "app.core"),
        ("agents/providers/boundary_violation.py", "app.core"),
        ("agents/providers/boundary_violation.py", "app.core"),
        ("agents/providers/boundary_violation.py", "app.core.scheduler"),
        ("agents/providers/boundary_violation.py", "app.roles.guard"),
    ]


def test_game_core_and_roles_do_not_import_provider_layer() -> None:
    offenders = imports_matching([APP / "core", APP / "roles"], "app.agents.providers")

    assert offenders == [], f"Game core and roles must not import provider modules: {offenders}"


def test_provider_layer_does_not_import_game_core_or_roles() -> None:
    offenders = imports_matching([APP / "agents" / "providers"], ("app.core", "app.roles"))

    assert offenders == [], f"Provider modules must not import game core or roles: {offenders}"
