"""Guard the cold-start import graph against re-adding the heavy SDK imports.

Importing ``anthropic`` / ``openai`` / ``langchain_openai`` on this project's
reference machine costs ~16 s because security software scans every opened
module file. The provider layer therefore imports them lazily; these AST checks
fail if a future edit moves one back to module scope.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).parents[1] / "app"


def _tree(relative: str) -> ast.Module:
    return ast.parse((APP / relative).read_text(encoding="utf-8"))


def _absolute_module_imports(relative: str) -> set[str]:
    names: set[str] = set()
    for node in _tree(relative).body:
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _relative_imports(relative: str) -> set[str]:
    return {
        node.module
        for node in _tree(relative).body
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
    }


def test_provider_errors_has_no_module_level_sdk_import() -> None:
    names = _absolute_module_imports("agents/providers/errors.py")
    assert not {
        name for name in names if name.split(".")[0] in {"anthropic", "openai"}
    }


def test_llm_client_has_no_module_level_langchain_openai_import() -> None:
    names = _absolute_module_imports("agents/llm_client.py")
    assert not {name for name in names if name.startswith("langchain_openai")}


def test_transports_does_not_import_implementations_at_module_level() -> None:
    assert not {
        "anthropic_messages", "openai_compatible", "openai_responses",
    } & _relative_imports("agents/providers/transports.py")


def test_prewarm_module_itself_has_no_module_level_sdk_import() -> None:
    names = _absolute_module_imports("agents/providers/prewarm.py")
    assert not {
        name for name in names if name.split(".")[0] in {"anthropic", "openai"}
    }
