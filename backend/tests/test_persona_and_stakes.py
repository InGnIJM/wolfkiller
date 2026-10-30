from __future__ import annotations

from app.agents.persona import (
    PERSONA_DECK, PERSONA_GUARD, persona_for, persona_line,
)
from app.agents.prompt_builder import PromptBuilder
from app.agents.stakes import stakes_lines, stakes_text
from app.core.conversation_log import ConversationLog
from app.core.night_flow import NightDirector
from app.core.sheriff_flow import SheriffDirector
from app.models.game import GameConfig, GameState, PlayerState
from app.roles.registry import RegistrySnapshot, builtin_registry


def _state(game_id: str = "g1") -> GameState:
    specs = builtin_registry.freeze().specs
    roles = [
        "wolf-killer-werewolf", "wolf-killer-werewolf", "wolf-killer-witch",
        "wolf-killer-seer", "wolf-killer-villager", "wolf-killer-villager",
    ]
    state = GameState(game_id=game_id, config=GameConfig(role_counts={
        "wolf-killer-werewolf": 2, "wolf-killer-witch": 1,
        "wolf-killer-seer": 1, "wolf-killer-villager": 2,
    }))
    for seat, role in enumerate(roles, start=1):
        state.players[seat] = PlayerState(
            seat_number=seat, role=role, camp=specs[role].camp_id,
        )
    state.round_number = 1
    return state


def test_persona_is_deterministic_and_covers_the_deck():
    assert persona_for("g", 3) == persona_for("g", 3)
    assert {persona_for("g", seat).persona_id for seat in range(1, 9)} == {
        card.persona_id for card in PERSONA_DECK
    }
    assert persona_for("g", 9) == persona_for("g", 1)


def test_persona_assignment_varies_with_the_game():
    orders = {
        tuple(persona_for(f"game-{i}", seat).persona_id for seat in range(1, 9))
        for i in range(10)
    }
    assert len(orders) > 1


def test_persona_line_carries_label_and_guard():
    line = persona_line("g", 1)
    assert persona_for("g", 1).label in line
    assert PERSONA_GUARD in line


def test_stakes_for_a_pack_wolf_is_exact():
    lines = stakes_lines(_state(), 1)
    assert "狼队存活 2，其余 4" in lines[1]
    assert "再减员 3 名好人" in lines[1]
    assert "；" in stakes_text(_state(), 1)


def test_stakes_for_a_good_seat_uses_only_public_facts():
    state = _state()
    state.players[2].is_alive = False
    state.players[2].revealed_role = "wolf-killer-werewolf"
    state.players[5].is_alive = False
    state.players[5].revealed_role = "not-a-registered-role"
    lines = stakes_lines(state, 3)
    assert "存活 4 人" in lines[0]
    assert "共 2 张牌，已公布出局 1 张，场上至多还有 1 只狼" in lines[1]
    assert "狼队存活" not in " ".join(lines)


def test_stakes_ignores_unregistered_board_roles():
    state = _state()
    state.config.role_counts["wolf-killer-unknown"] = 3
    assert "共 2 张牌" in stakes_lines(state, 3)[1]


def test_stakes_ceiling_never_goes_negative():
    state = _state()
    state.config.role_counts = {"wolf-killer-villager": 6}
    state.players[1].is_alive = False
    state.players[1].revealed_role = "wolf-killer-werewolf"
    assert "至多还有 0 只狼" in stakes_lines(state, 3)[1]


def test_prompts_carry_persona_and_stakes():
    state = _state()
    log = ConversationLog()
    builder = PromptBuilder()
    view = builder.state_filter.filter_for_role(state, 3, "wolf-killer-witch")
    base = builder._build_base(state, 3, view, log)
    assert persona_line("g1", 3) in base
    assert "局势距离：" in base
    retry = builder.build_vote_retry_prompt(state, 3, "wolf-killer-witch", log)
    assert persona_line("g1", 3) in retry

    director = NightDirector(
        RegistrySnapshot(specs={}, digest="t"), lambda *_args: "{}",
    )
    for messages in (
        director.discussion_prompt(state, 1, []),
        director.vote_prompt(state, 1, [], []),
    ):
        text = messages[0]["content"] + messages[1]["content"]
        assert persona_for("g1", 1).label in text
        assert "局势距离：" in text

    sheriff = SheriffDirector(lambda *_args: "{}", log)
    block = sheriff._context_block(state, 3, wolf=False)
    assert persona_line("g1", 3) in block
    assert "局势距离：" in block


def test_tactical_advocate_rotates_over_the_pack():
    state = _state()
    director = NightDirector(
        RegistrySnapshot(specs={}, digest="t"), lambda *_args: "{}",
    )
    assert "战术倡导者" in director._advocate_clause(state, 1)
    assert director._advocate_clause(state, 2) == ""
    state.round_number = 2
    assert "战术倡导者" in director._advocate_clause(state, 2)
    state.players[2].is_alive = False
    assert director._advocate_clause(state, 1) == ""


def test_self_kill_is_no_longer_gated_on_unanimity():
    state = _state()
    director = NightDirector(
        RegistrySnapshot(specs={}, digest="t"), lambda *_args: "{}",
    )
    clause = director._self_kill_clause(state, 1)
    assert "including yourself" in clause
    assert "无需全员同意" in clause
    assert "指定由哪位队友担任刀口" in clause
