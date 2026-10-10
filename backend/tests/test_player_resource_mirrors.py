"""The three legacy resource mirrors must stay read-only.

``PlayerState.has_antidote`` / ``has_poison`` / ``has_gun`` were the engine's own
resource flags until the generic role pipeline moved every resource into
``runtime.role_resources`` (2026-08-13). They stay in the dataclass because the
checkpoint decoder checks the player row's field set *exactly* — dropping them
would make every existing checkpoint fail to decode — but nothing may write them
again. A field that one layer writes and another reads is exactly how "there is
no runtime yet" hid as "no gun".

The reads are gone too (``ContextProjector._actor_resources`` and
``MemoryService._build_private_knowledge`` read the canonical view only), which
``test_declared_resources_read_their_declaration_not_the_legacy_mirror`` and
``test_potions_read_the_canonical_view_only`` pin.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
MIRRORS = ("has_antidote", "has_poison", "has_gun")


def _production_modules() -> list[Path]:
    return sorted(
        path for path in APP.rglob("*.py") if "__pycache__" not in path.parts
    )


def test_no_production_code_writes_a_resource_mirror() -> None:
    """No attribute assignment and no ``setattr`` on the three mirrors."""
    offenders: list[str] = []
    for path in _production_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr in MIRRORS:
                    offenders.append(
                        f"{path.relative_to(APP.parent)}:{target.lineno}"
                    )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in MIRRORS
            ):
                offenders.append(f"{path.relative_to(APP.parent)}:{node.lineno}")

    assert offenders == []


def test_the_mirror_fields_are_still_part_of_the_checkpoint_row() -> None:
    """They cannot be deleted: the decoder's field set is exact and symmetric.

    ``_exact`` compares ``set(row)`` with the dataclass field names, so removing
    a field rejects every archived checkpoint that still carries it — a hard
    ``CheckpointError``, not a ``drift`` a caller can confirm past.
    """
    from app.models.game import PlayerState
    from app.persistence.checkpoint_codec import _PLAYER_FIELDS

    assert set(MIRRORS) <= _PLAYER_FIELDS
    assert set(MIRRORS) <= {field for field in PlayerState.__dataclass_fields__}
