"""Role instructions must agree with the sets the hooks actually enforce.

`instructions` travels into the prompt verbatim, and the project's own rule is
"use only the rules stated in this prompt". When the text and the code disagree,
the model is being taught a rule the engine will reject — which is how a Wolf
King came to believe its victim could shoot back, and how a Hunter came to
believe a white-wolf-king explosion still left it a gun.
"""

from __future__ import annotations

from app.roles.dancer import DANCER_SPEC
from app.roles.hunter import HUNTER_SPEC, _SHOOT_REASONS
from app.roles.witch import WITCH_SPEC
from app.roles.wolf_king import WOLF_KING_SPEC, _CLAW_REASONS


def test_hunter_instructions_match_its_own_reasons() -> None:
    text = HUNTER_SPEC.instructions
    assert _SHOOT_REASONS == frozenset({"wolf_kill", "exile"})
    assert "夜间被狼刀、白天被放逐" in text
    # The old text promised a gun after a white-wolf-king explosion.
    assert "即使被自爆带走也能开枪" not in text
    for excluded in ("白狼王自爆带走", "狼王爪带走", "被女巫毒死"):
        assert excluded in text


def test_wolf_king_instructions_do_not_promise_a_shooting_hunter() -> None:
    text = WOLF_KING_SPEC.instructions
    assert _CLAW_REASONS == frozenset({"wolf_kill", "exile", "hunter_shot"})
    assert "被狼王爪带走的猎人**不能**开枪" in text
    assert "被带走的猎人仍然可以开枪反击" not in text


def test_witch_instructions_defer_the_self_save_rule_to_the_game_setting() -> None:
    """The setting is per game; the instruction used to hard-code one value."""
    text = WITCH_SPEC.instructions
    assert "witch_self_save" in text
    assert "first_night_only" in text and "never" in text
    assert "saving yourself is allowed only on the first night" not in text


def test_dancer_instructions_name_the_seats_it_may_not_reuse() -> None:
    """`alive_seats` still lists pooled seats; only the rule text can exclude them."""
    text = DANCER_SPEC.instructions
    assert "pooled_seats" in text
    assert "不能再选" in text
    assert "不会再出现在可选名单里" not in text
