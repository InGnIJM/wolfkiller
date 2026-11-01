"""Coverage-focused edge paths for the two awakened cards' defensive hooks."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidator
from app.core.context_projector import ContextProjector
from app.core.effect_applier import EffectApplier
from app.core.night_flow import WolfVote
from app.core.scheduler import Scheduler
from app.models.game import GameState
from app.models.pipeline import (
    DEGRADED_THOUGHT, ActionCommand, ActionContext, SchedulePoint,
)
from app.roles.registry import builtin_registry
from app.roles.awakened_witch import (
    ASSIST_HISTORY_KEY, AWAKENED_WITCH_ASSIST_VOTE_CONTRACT,
    AWAKENED_WITCH_BREW_CONTRACT, AWAKENED_WITCH_PICK_1_CONTRACT,
    AWAKENED_WITCH_PICK_2_CONTRACT, AWAKENED_WITCH_TALLY_CONTRACT,
    _command_targets, _kill_target, _pick_validate, _plan,
    _plan_assist_seats, _plan_target, _plan_use_index, _resolve_pick,
    _selected_labels, _selected_rows, _tally_votes, _vote_target,
    awakened_witch_assist_pick_applicable, resolve_awakened_witch_assist_tally,
    resolve_awakened_witch_assist_vote, resolve_awakened_witch_brew,
    validate_awakened_witch_assist_vote, validate_awakened_witch_brew,
)
from app.roles.awakened_wolf_king import (
    _identity, resolve_awakened_wolf_king_explode,
    validate_awakened_wolf_king_explode,
)
from app.roles.wolf_claw import (
    AWAKENED_CLAW_CONTRACT, AWAKENED_CLAW_GIFT_CONTRACT,
    AWAKENED_CLAW_GIFT_KEY, AWAKENED_CLAW_PASS_CONTRACT,
    _last_wolf_standing, _living_teammates,
    _selected_resource_labels, _thought_for,
    resolve_awakened_claw_action, resolve_awakened_claw_pass,
    resolve_awakened_claw_sweep, validate_awakened_claw_action,
    validate_awakened_claw_gift, validate_awakened_claw_pass,
    validate_awakened_claw_sweep,
)


def _ctx(contract, *, facts=None, resources=None, round_number=1, actor_alive=True):
    return ActionContext(
        "g", 0, facts or {}, config_version="a" * 64,
        contract_id=contract.contract_id, contract_version=contract.schema_version,
        contract_digest=contract.stable_digest(), round_number=round_number,
        phase="night", window_id="w", schedule_point=contract.schedule_point,
        actor_seat=1, actor_role_id=contract.role_id
        if hasattr(contract, "role_id") else "wolf-killer-awakened-witch",
        actor_alive=actor_alive, resources=resources or {}, action_key="a",
    )


def _cmd(action, target=None, targets=None, *, degraded=False):
    return ActionCommand(
        action_type=action, target_seat=target, target_seats=targets,
        reasoning="ok", degraded=degraded,
    )


# ── Awakened Witch defensive helpers ───────────────────────────────────────


def test_witch_private_plan_helpers_reject_malformed_values() -> None:
    with pytest.raises(TypeError):
        _kill_target(_ctx(AWAKENED_WITCH_BREW_CONTRACT, facts={"wolf_kill_target": "x"}))
    assert _kill_target(_ctx(AWAKENED_WITCH_BREW_CONTRACT)) is None
    assert _plan(_ctx(AWAKENED_WITCH_BREW_CONTRACT)) is None
    with pytest.raises(TypeError):
        _plan(_ctx(AWAKENED_WITCH_BREW_CONTRACT, facts={"brew_plan": "x"}))
    with pytest.raises(TypeError):
        _plan_target({"target": "x"})
    with pytest.raises(TypeError):
        _plan_use_index({"use_index": 9})
    with pytest.raises(TypeError):
        _plan_assist_seats({"assist_seats": "x"})
    with pytest.raises(TypeError):
        _plan_assist_seats({"assist_seats": (1, "x")})
    assert _selected_rows(
        _ctx(AWAKENED_WITCH_PICK_2_CONTRACT, facts={"selected_targets": ({"seat": 1},)})
    ) == ({"seat": 1},)
    with pytest.raises(TypeError):
        _selected_rows(_ctx(AWAKENED_WITCH_PICK_2_CONTRACT, facts={"selected_targets": "x"}))
    with pytest.raises(TypeError):
        _selected_rows(_ctx(AWAKENED_WITCH_PICK_2_CONTRACT, facts={"selected_targets": (1,)}))
    with pytest.raises(TypeError):
        _selected_labels({"resource_labels": "x"})
    with pytest.raises(TypeError):
        _vote_target(_ctx(AWAKENED_WITCH_ASSIST_VOTE_CONTRACT))
    with pytest.raises(TypeError):
        _tally_votes(_ctx(AWAKENED_WITCH_TALLY_CONTRACT, facts={"assist_votes": "x"}))


def test_witch_brew_defensive_branches() -> None:
    ctx = _ctx(
        AWAKENED_WITCH_BREW_CONTRACT,
        facts={"wolf_kill_target": 2, "witch_self_save": "first_night_only"},
        resources={"antidote": 0, "brew": 3, "brew_used": 0},
    )
    assert validate_awakened_witch_brew(
        ctx, _cmd("save", 2)
    )[0].code == "antidote_unavailable"
    assert validate_awakened_witch_brew(
        _ctx(
            AWAKENED_WITCH_BREW_CONTRACT,
            facts={"wolf_kill_target": 1, "witch_self_save": "first_night_only"},
            resources={"antidote": 1, "brew": 3, "brew_used": 0},
            round_number=2,
        ),
        _cmd("save", 1),
    )[0].code == "self_save_first_night_only"
    assert validate_awakened_witch_brew(
        ctx, _cmd("poison")
    )[0].code == "brew_target_required"
    assert validate_awakened_witch_brew(
        _ctx(
            AWAKENED_WITCH_BREW_CONTRACT,
            facts={"wolf_kill_target": 1, "witch_self_save": "first_night_only"},
            resources={"antidote": 1, "brew": 3, "brew_used": 0},
        ),
        _cmd("save", 1),
    ) == ()
    assert resolve_awakened_witch_brew(
        _ctx(AWAKENED_WITCH_BREW_CONTRACT),
        _cmd("pass", degraded=True),
    )[0].payload["payload"]["thought"] == DEGRADED_THOUGHT
    assert resolve_awakened_witch_brew(
        _ctx(AWAKENED_WITCH_BREW_CONTRACT), _cmd("pass")
    )[0].payload["payload"]["thought"].startswith("决定今晚不使用药水")
    with pytest.raises(TypeError):
        resolve_awakened_witch_brew(
            _ctx(
                AWAKENED_WITCH_BREW_CONTRACT,
                resources={"brew": 1, "brew_used": 3},
            ),
            _cmd("poison", 3),
        )


def test_witch_pick_applicability_and_validation_edges() -> None:
    no_plan = _ctx(AWAKENED_WITCH_PICK_1_CONTRACT)
    assert awakened_witch_assist_pick_applicable(no_plan) is False
    unknown = replace(no_plan, contract_id="unknown")
    assert awakened_witch_assist_pick_applicable(unknown) is False
    dead = replace(no_plan, actor_alive=False)
    assert awakened_witch_assist_pick_applicable(dead) is False
    with_plan = _ctx(
        AWAKENED_WITCH_PICK_1_CONTRACT,
        facts={"brew_plan": {"round": 1, "use_index": 1, "assist_seats": ()}},
    )
    assert awakened_witch_assist_pick_applicable(with_plan) is True
    already = _ctx(
        AWAKENED_WITCH_PICK_1_CONTRACT,
        facts={"brew_plan": {"round": 1, "use_index": 1, "assist_seats": (1,)}},
    )
    assert awakened_witch_assist_pick_applicable(already) is False
    assert _command_targets(_cmd("pass")) == ()
    assert _pick_validate(with_plan, _cmd("pass")) == ()
    assert _pick_validate(no_plan, _cmd("assist", 1))[0].code == "brew_plan_missing"
    assert _pick_validate(
        _ctx(
            AWAKENED_WITCH_PICK_2_CONTRACT,
            facts={
                "brew_plan": {"round": 1, "use_index": 2, "assist_seats": ()},
                "selected_targets": ({"seat": 1, "resource_labels": ()},),
            },
        ),
        _cmd("assist", targets=(1, 3)),
    ) == ()
    assert _pick_validate(
        _ctx(
            AWAKENED_WITCH_PICK_1_CONTRACT,
            facts={
                "brew_plan": {"round": 1, "use_index": 1, "assist_seats": ()},
                "selected_target": {"seat": 1, "resource_labels": ()},
            },
        ),
        _cmd("assist", 1),
    ) == ()


def test_witch_pick_resolution_edges() -> None:
    assert _resolve_pick(_ctx(AWAKENED_WITCH_PICK_1_CONTRACT), _cmd("pass")) == ()
    base_facts = {
        "brew_plan": {
            "target": 3, "round": 1, "use_index": 1, "assist_seats": (),
        },
        ASSIST_HISTORY_KEY: (),
        "selected_target": {"seat": 1, "resource_labels": ()},
    }
    ctx = _ctx(AWAKENED_WITCH_PICK_1_CONTRACT, facts=base_facts)
    assert _resolve_pick(ctx, _cmd("assist", 1))
    no_target = _ctx(AWAKENED_WITCH_PICK_1_CONTRACT, facts=base_facts)
    assert _pick_validate(no_target, _cmd("assist")) == ()
    with pytest.raises(TypeError):
        _resolve_pick(
            _ctx(
                AWAKENED_WITCH_PICK_1_CONTRACT,
                facts={
                    **base_facts,
                    ASSIST_HISTORY_KEY: "bad",
                },
            ),
            _cmd("assist", 1),
        )
    with pytest.raises(TypeError):
        _resolve_pick(
            _ctx(
                AWAKENED_WITCH_PICK_1_CONTRACT,
                facts={
                    **base_facts,
                    "brew_plan": {"target": "bad"},
                },
            ),
            _cmd("pass"),
        )
    degraded = _resolve_pick(
        _ctx(
            AWAKENED_WITCH_PICK_1_CONTRACT,
            facts={
                **base_facts,
                "brew_plan": {
                    "target": 3, "round": 1, "use_index": 1, "assist_seats": (),
                },
            },
        ),
        _cmd("pass", degraded=True),
    )
    assert degraded[2].payload["payload"]["thought"] == DEGRADED_THOUGHT


def test_witch_vote_and_tally_edges() -> None:
    vote_ctx = _ctx(
        AWAKENED_WITCH_ASSIST_VOTE_CONTRACT,
        resources={"assist_token": 1, "assist_target": 3, "assist_round": 1},
    )
    assert resolve_awakened_witch_assist_vote(
        vote_ctx, _cmd("pass")
    )[2].payload["payload"]["vote"] == "pass"
    assert validate_awakened_witch_assist_vote(
        vote_ctx, _cmd("veto", 3)
    ) == ()
    with pytest.raises(TypeError):
        _vote_target(_ctx(AWAKENED_WITCH_ASSIST_VOTE_CONTRACT))
    plan = {
        "target": 3, "round": 1, "use_index": 1, "assist_count": 1,
        "assist_seats": (1,),
    }
    assert resolve_awakened_witch_assist_tally(
        _ctx(AWAKENED_WITCH_TALLY_CONTRACT), _cmd("resolve")
    ) == ()
    with pytest.raises(TypeError):
        resolve_awakened_witch_assist_tally(
            _ctx(AWAKENED_WITCH_TALLY_CONTRACT, facts={"brew_plan": "bad"}),
            _cmd("resolve"),
        )


# ── Awakened Wolf King / claw defensive branches ───────────────────────────


def test_awk_explode_edges_and_legacy_class() -> None:
    base = _ctx(AWAKENED_WITCH_BREW_CONTRACT)
    assert _last_wolf_standing(
        replace(base, facts={"camp_members": "bad", "alive_seats": ()})
    ) is False
    with pytest.raises(TypeError):
        _living_teammates(
            replace(base, facts={"camp_members": "bad", "alive_seats": ()})
        )
    with pytest.raises(ValueError):
        _selected_resource_labels(base)
    with pytest.raises(TypeError):
        _selected_resource_labels(
            replace(base, facts={"selected_target": {"resource_labels": "bad"}})
        )
    assert _thought_for(base, _cmd("pass", degraded=True)) == DEGRADED_THOUGHT
    assert "决定暂不自爆" in resolve_awakened_wolf_king_explode(
        base, _cmd("pass")
    )[0].payload["payload"]["thought"]
    assert "决定不发动狼王爪" in _thought_for(base, _cmd("sweep"))
    assert validate_awakened_wolf_king_explode(base, _cmd("pass")) == ()
    explode_ctx = replace(base, resources={"explode": 0})
    assert validate_awakened_wolf_king_explode(
        explode_ctx, _cmd("explode")
    )[0].code == "explode_unavailable"
    assert validate_awakened_wolf_king_explode(
        replace(base, resources={"explode": 1}), _cmd("explode")
    ) == ()
    assert resolve_awakened_wolf_king_explode(
        base, _cmd("pass", degraded=True)
    )[0].payload["payload"]["thought"] == DEGRADED_THOUGHT
    assert resolve_awakened_wolf_king_explode(base, _cmd("pass"))[0].payload[
        "payload"
    ]["action_type"] == "pass"
    assert _identity(base) == ("wolf-killer-awakened-witch", "werewolf")
    assert _identity(
        replace(base, facts={"actor_identity": "bad"})
    ) == ("wolf-killer-awakened-witch", "werewolf")
    assert _identity(
        replace(base, facts={"actor_identity": {"role_id": 1, "camp_id": 2}})
    ) == ("wolf-killer-awakened-witch", "werewolf")


def test_claw_defensive_branches() -> None:
    base = _ctx(AWAKENED_CLAW_CONTRACT, facts={"camp_members": (1, 2), "alive_seats": (1, 2)})
    assert validate_awakened_claw_action(base, _cmd("pass")) == ()
    assert validate_awakened_claw_sweep(base, _cmd("pass")) == ()
    assert resolve_awakened_claw_action(base, _cmd("pass"))[0].payload["payload"][
        "thought"
    ].startswith("决定不发动狼王爪")
    assert resolve_awakened_claw_sweep(base, _cmd("pass"))[0].payload["payload"][
        "thought"
    ].startswith("决定不发动狼王爪")
    assert resolve_awakened_claw_pass(
        _ctx(AWAKENED_CLAW_PASS_CONTRACT), _cmd("pass")
    )[0].payload["payload"]["action_type"] == "pass"
    assert resolve_awakened_claw_pass(
        _ctx(AWAKENED_CLAW_PASS_CONTRACT), _cmd("pass", degraded=True)
    )[0].payload["payload"]["thought"] == DEGRADED_THOUGHT
    assert validate_awakened_claw_pass(
        _ctx(AWAKENED_CLAW_PASS_CONTRACT), _cmd("pass")
    ) == ()
    assert validate_awakened_claw_pass(
        _ctx(
            AWAKENED_CLAW_PASS_CONTRACT,
            facts={
                "camp_members": (1, 2), "alive_seats": (1, 2),
                "selected_target": {"seat": 2, "resource_labels": ()},
            },
        ),
        _cmd("pass_claw", 2),
    ) == ()
    assert validate_awakened_claw_gift(
        _ctx(AWAKENED_CLAW_GIFT_CONTRACT, facts={AWAKENED_CLAW_GIFT_KEY: "x"}),
        _cmd("pass"),
    ) == ()
    assert validate_awakened_claw_gift(
        _ctx(AWAKENED_CLAW_GIFT_CONTRACT, facts={AWAKENED_CLAW_GIFT_KEY: "x"}),
        _cmd("shoot"),
    )[0].code == "awakened_claw_target_required"


def test_wolf_vote_rejects_a_non_bool_degraded_flag() -> None:
    from app.core.night_flow import WolfVote

    with pytest.raises(ValueError):
        WolfVote(1, "pass", None, "x", degraded=1)


def test_scheduler_point_completed_rejects_bad_types_and_empty_journal() -> None:
    scheduler = Scheduler(
        builtin_registry.freeze(), ContextProjector(), ActionValidator(),
        ActionResolver(), EffectApplier(), lambda *args: None,
    )
    with pytest.raises(TypeError):
        scheduler.point_completed("bad", SchedulePoint.NIGHT_ACTION)
    with pytest.raises(TypeError):
        scheduler.point_completed(GameState("g"), "bad")
    assert scheduler.point_completed(
        GameState("g"), SchedulePoint.NIGHT_ACTION
    ) is False
