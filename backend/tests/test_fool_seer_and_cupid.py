"""Stage 4.6 / 4.7: allegiance, self-reveal, the crow's curse, the link."""

from __future__ import annotations

from app.core.context_projector import ContextProjector
from app.core.effect_applier import (
    EffectApplier, EffectPermission, _Runtime, derive_effect_id,
)
from app.core.game_engine import GameEngine
from app.core.reflect import REFLECT_CHECK_RESOURCE, REFLECT_USED_RESOURCE
from app.core.rule_engine import RuleEngine
from app.core.vote_service import CURSE_STATUS, VoteService
from app.core.win_attribution import (
    CRUSH_KEY, LOVER_PAIR_KEY, LOVER_PEER_KEY, seat_allegiances, winner_seats,
)
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, EffectKind, GameEffect, SchedulePoint,
)
from app.models.vote import CastVoteArgs
from dataclasses import replace

from app.roles.admirer import admirer_applicable, resolve_admirer_action, validate_admirer_action
from app.roles.crow import crow_applicable, resolve_crow_action, validate_crow_action
from app.roles.cupid import (
    chain_applicable, cupid_applicable, react_cupid_chain, resolve_cupid_link,
    validate_cupid_link,
)
from app.roles.fool_seer import (
    fool_seer_applicable, resolve_fool_seer_action, resolve_fool_seer_reveal,
    validate_fool_seer_action,
)
from app.roles.iron_villager import resolve_iron_villager_reveal
from app.roles.self_reveal import reveal_applicable, resolve_reveal_action, validate_reveal_action


def _state(*rows: tuple[int, str, str], victory: str = "edge") -> GameState:
    players = {
        seat: PlayerState(seat, role, camp) for seat, role, camp in rows
    }
    state = GameState(
        "board", phase=GamePhase.NIGHT, round_number=1,
        config=GameConfig(role_counts={"wolf-killer-villager": 1}, victory=victory),
        players=players,
    )
    state._pipeline_runtime = _Runtime()
    return state


def _put(state: GameState, seat: int, key: str, value: object) -> None:
    state._pipeline_runtime.private_data.setdefault(seat, {})[key] = value


def _context(*, resources: dict[str, int] | None = None, **facts: object) -> ActionContext:
    if resources is None:
        resources = {"reveal": 1, "love_available": 1, "crush_available": 1}
    return ActionContext(
        game_id="board", revision=0, config_version="registry",
        contract_id="check", contract_version=1, contract_digest="0" * 64,
        round_number=1, phase="night", window_id="w",
        schedule_point=SchedulePoint.NIGHT_ACTION,
        actor_seat=1, actor_role_id="wolf-killer-fool-seer", actor_alive=True,
        action_key="a", facts=facts, resources=resources,
    )


def test_no_link_keeps_the_stored_camp_and_the_historical_winner_shape() -> None:
    state = _state(
        (1, "wolf-killer-werewolf", "werewolf"),
        (2, "wolf-killer-villager", "good"),
        (3, "wolf-killer-seer", "good"),
    )
    assert seat_allegiances(state) == {1: "werewolf", 2: "good", 3: "good"}
    assert winner_seats(state, "good") is None
    assert RuleEngine().check_win(state) is None


def test_a_mixed_pair_is_a_third_party_and_blocks_both_sides() -> None:
    state = _state(
        (1, "wolf-killer-cupid", "third_party"),
        (2, "wolf-killer-werewolf", "werewolf"),
        (3, "wolf-killer-villager", "good"),
        (4, "wolf-killer-seer", "good"),
    )
    _put(state, 1, LOVER_PAIR_KEY, [2, 3])
    assert seat_allegiances(state)[1] == "third_party"
    assert seat_allegiances(state)[2] == "third_party"
    assert seat_allegiances(state)[3] == "third_party"
    assert seat_allegiances(state)[4] == "good"
    assert RuleEngine().check_win(state) is None
    state.players[4].is_alive = False
    state.players[1].is_alive = False
    state.players[3].is_alive = False
    # Only the wolf lover remains, and that seat is still third party.
    result = RuleEngine().check_win(state)
    assert result is not None and result.winning_camp == "third_party"
    assert result.reason == "all_others_dead"


def test_the_last_third_party_seats_win_and_a_crush_follows_them() -> None:
    state = _state(
        (1, "wolf-killer-cupid", "third_party"),
        (2, "wolf-killer-werewolf", "werewolf"),
        (3, "wolf-killer-villager", "good"),
        (5, "wolf-killer-admirer", "good"),
    )
    _put(state, 1, LOVER_PAIR_KEY, [2, 3])
    _put(state, 5, CRUSH_KEY, {"seat": 2})
    state.players[1].is_alive = False
    for seat in (1,):
        state.players[seat].is_alive = False
    # Kill everyone who is not third party. 4 does not exist. 5 follows 2.
    assert seat_allegiances(state)[5] == "third_party"
    result = RuleEngine().check_win(state)
    assert result is not None
    assert result.winning_camp == "third_party"
    assert result.reason == "all_others_dead"
    assert result.winners is not None
    assert 5 in result.winners and 2 in result.winners


def test_same_camp_links_move_the_linker_and_city_waits_for_an_empty_side() -> None:
    state = _state(
        (1, "wolf-killer-cupid", "third_party"),
        (2, "wolf-killer-villager", "good"),
        (3, "wolf-killer-villager", "good"),
        (4, "wolf-killer-werewolf", "werewolf"),
        victory="city",
    )
    _put(state, 1, LOVER_PAIR_KEY, {"seats": [2, 3]})
    assert seat_allegiances(state)[1] == "good"
    assert RuleEngine().check_win(state) is None
    state.players[2].is_alive = False
    state.players[3].is_alive = False
    state.players[1].is_alive = False
    wolf = RuleEngine().check_win(state)
    assert wolf is not None and wolf.reason == "all_good_dead"
    state.players[4].is_alive = False
    state.players[2].is_alive = True
    good = RuleEngine().check_win(state)
    assert good is not None and good.winning_camp == "good"


def test_a_wolf_pair_counts_the_linker_as_a_wolf_on_an_edge_board() -> None:
    state = _state(
        (1, "wolf-killer-cupid", "third_party"),
        (2, "wolf-killer-werewolf", "werewolf"),
        (3, "wolf-killer-werewolf", "werewolf"),
        (4, "wolf-killer-villager", "good"),
        (5, "wolf-killer-seer", "good"),
    )
    _put(state, 1, LOVER_PAIR_KEY, [2, 3])
    assert seat_allegiances(state)[1] == "werewolf"
    state.players[4].is_alive = False
    result = RuleEngine().check_win(state)
    assert result is not None and result.reason == "all_villagers_dead"


def test_a_crush_cycle_stops_on_the_seat_it_was_about_to_repeat() -> None:
    state = _state((1, "wolf-killer-admirer", "good"), (2, "wolf-killer-werewolf", "werewolf"))
    _put(state, 1, CRUSH_KEY, {"seat": 2})
    _put(state, 2, CRUSH_KEY, {"seat": 1})
    assert seat_allegiances(state)[1] == "werewolf"
    assert seat_allegiances(state)[2] == "good"


def test_malformed_private_slots_do_not_move_anyone() -> None:
    state = _state((1, "wolf-killer-cupid", "third_party"), (2, "wolf-killer-villager", "good"))
    _put(state, 1, LOVER_PAIR_KEY, [2, 2])
    assert seat_allegiances(state) == {1: "third_party", 2: "good"}
    _put(state, 1, LOVER_PAIR_KEY, [2])
    _put(state, 1, CRUSH_KEY, {"seat": 99})
    _put(state, 2, LOVER_PAIR_KEY, "nope")
    assert seat_allegiances(state) == {1: "third_party", 2: "good"}


def test_an_unlinked_third_party_blocks_until_they_are_the_last_side() -> None:
    state = _state(
        (1, "wolf-killer-cupid", "third_party"),
        (2, "wolf-killer-villager", "good"),
        victory="city",
    )
    assert RuleEngine().check_win(state) is None
    state.players[2].is_alive = False
    result = RuleEngine().check_win(state)
    assert result is not None and result.winning_camp == "third_party"


def test_a_check_reads_the_current_allegiance_and_a_lover_hears_the_seat() -> None:
    state = _state(
        (1, "wolf-killer-fool-seer", "good"),
        (2, "wolf-killer-werewolf", "werewolf"),
        (3, "wolf-killer-cupid", "third_party"),
    )
    _put(state, 3, LOVER_PAIR_KEY, [1, 2])
    _put(state, 1, LOVER_PEER_KEY, {"seat": 2, "round": 1})
    row = ContextProjector()._selected_target_row(state, 1, frozenset({"camp_label"}))
    assert row is not None and row["camp_label"] == "third_party"
    note = ContextProjector._held_lover_facts(state, state.players[1], {})
    assert note[LOVER_PEER_KEY] == "你与 2 号是挚友，不知道对方身份。"
    assert ContextProjector._held_lover_facts(state, state.players[1], {LOVER_PEER_KEY: "x"}) == {}
    assert ContextProjector._held_lover_facts(state, state.players[2], {}) == {}
    _put(state, 2, LOVER_PEER_KEY, {"seat": 99})
    assert ContextProjector._held_lover_facts(state, state.players[2], {}) == {}
    _put(state, 2, LOVER_PEER_KEY, {"seat": 2})
    assert ContextProjector._held_lover_facts(state, state.players[2], {}) == {}


def test_the_fool_seer_check_records_the_camp_and_refuses_itself() -> None:
    context = _context(selected_target={"seat": 2, "camp_label": "werewolf"})
    command = ActionCommand(action_type="check", target_seat=2, reasoning="验")
    effects = resolve_fool_seer_action(context, command)
    assert effects[0].payload["fact"] == {"target": 2, "camp": "werewolf"}
    assert effects[2].payload["event_type"] == "FOOL_SEER_CHECK"
    assert validate_fool_seer_action(
        context, ActionCommand(action_type="check", target_seat=1, reasoning="x"),
    )


def test_a_locked_check_lands_on_the_checker_and_a_reflect_bounces() -> None:
    locked = _context(selected_target={
        "seat": 2, "camp_label": "werewolf", "status_labels": ("time_locked",),
    })
    effects = resolve_fool_seer_action(
        locked, ActionCommand(action_type="check", target_seat=2, reasoning="锁"),
    )
    assert effects[0].payload["fact"]["camp"] == "good"
    assert effects[0].payload["fact"]["target"] == 1
    bounced = _context(selected_target={
        "seat": 2, "camp_label": "werewolf",
        "resource_labels": (REFLECT_CHECK_RESOURCE,),
    })
    reflected = resolve_fool_seer_action(
        bounced, ActionCommand(action_type="check", target_seat=2, reasoning="反"),
    )
    assert any(effect.kind is EffectKind.SUBMIT_DAMAGE for effect in reflected)
    spent = _context(selected_target={
        "seat": 2, "camp_label": "good",
        "resource_labels": (REFLECT_CHECK_RESOURCE, REFLECT_USED_RESOURCE),
    })
    quiet = resolve_fool_seer_action(
        spent, ActionCommand(action_type="check", target_seat=2, reasoning="已用"),
    )
    assert all(effect.kind is not EffectKind.SUBMIT_DAMAGE for effect in quiet)


def test_a_pass_check_and_a_bad_fact_are_both_answered() -> None:
    context = _context()
    assert resolve_fool_seer_action(
        context, ActionCommand(action_type="pass", reasoning="不验"),
    )[0].payload["payload"]["action_type"] == "pass"
    try:
        resolve_fool_seer_action(
            context, ActionCommand(action_type="check", target_seat=2, reasoning="缺"),
        )
    except ValueError:
        pass
    else:
        raise AssertionError("missing fact")
    try:
        resolve_fool_seer_action(
            _context(selected_target={"seat": 2, "camp_label": 1}),
            ActionCommand(action_type="check", target_seat=2, reasoning="坏"),
        )
    except TypeError:
        pass
    else:
        raise AssertionError("bad camp")


def test_self_reveal_shows_the_card_and_a_pass_keeps_it() -> None:
    shown = resolve_reveal_action(
        _context(actor_identity={"role_id": "wolf-killer-fool-seer", "camp_id": "good"}),
        ActionCommand(action_type="reveal", reasoning="亮"),
        event_type="FOOL_SEER_REVEAL_REASONING",
    )
    assert shown[1].payload["event_type"] == "PLAYER_REVEALED"
    assert shown[1].payload["payload"]["role"] == "wolf-killer-fool-seer"
    assert shown[0].kind is EffectKind.CONSUME_RESOURCE
    passed = resolve_reveal_action(
        _context(), ActionCommand(action_type="pass", reasoning="藏"),
        event_type="IRON_VILLAGER_REASONING",
    )
    assert passed[0].payload["payload"]["action_type"] == "pass"
    bare = resolve_reveal_action(
        _context(actor_identity="nope"),
        ActionCommand(action_type="reveal", reasoning="亮"),
        event_type="IRON_VILLAGER_REASONING",
    )
    assert bare[1].payload["payload"]["role"] == "wolf-killer-fool-seer"
    assert validate_reveal_action(
        _context(resources={}), ActionCommand(action_type="reveal", reasoning="x"),
    )
    assert validate_reveal_action(
        _context(), ActionCommand(action_type="reveal", target_seat=2, reasoning="x"),
    )


def test_the_engine_records_a_flip_that_does_not_cancel_the_exile() -> None:
    engine = GameEngine("flip")
    engine.state.players[4] = PlayerState(4, "wolf-killer-fool-seer", "good")
    engine._note_card_flips((
        ("PLAYER_REVEALED", {"seat_number": 4, "role": "wolf-killer-fool-seer", "camp": "good"}),
    ))
    assert engine.state.players[4].revealed_role == "wolf-killer-fool-seer"
    assert any("翻牌" in record.content for record in engine.conversation_log.records)
    engine._note_card_flips((
        ("PLAYER_REVEALED", {"seat_number": 4}),
        ("EXILE_CANCELLED", {}),
    ))
    engine.state.players[5] = PlayerState(5, "missing-role", "good")
    engine._note_card_flips((("VOTE_REWOUND", {}), ("PLAYER_REVEALED", {"seat_number": 5})))
    assert engine.state.players[5].revealed_role == "missing-role"
    engine._note_card_flips((("PLAYER_REVEALED", {"seat_number": "x"}), ("PLAYER_REVEALED", {})))
    engine.state.players[6] = PlayerState(6, "missing-role", "good")
    engine._note_card_flips((("PLAYER_REVEALED", {"seat_number": 6}),))
    assert any("missing-role" in record.content for record in engine.conversation_log.records)
    engine._note_card_flips((("PLAYER_REVEALED", {"seat_number": 99}),))


def test_the_curse_adds_one_exile_ballot_and_clears_after_the_tally() -> None:
    state = GameState(game_id="vote", phase=GamePhase.VOTE_CASTING, round_number=1)
    state.players = {seat: PlayerState(seat, "wolf-killer-villager", "good") for seat in (1, 2, 3)}
    service = VoteService(state, clock=lambda: 1.0)
    window = service.open_window(timeout_seconds=30.0)
    service.submit(window.window_id, 1, CastVoteArgs(action_type="vote", target_seat=2, reasoning="票"))
    state._pipeline_runtime.statuses[2] = {"cursed"}
    state._pipeline_runtime.statuses[3] = {CURSE_STATUS}
    assert service.tally(window.window_id)[2] == 2
    assert service.tally(window.window_id)[3] == 1
    service.clear_tally_marks()
    assert CURSE_STATUS not in state._pipeline_runtime.statuses.get(2, set())
    service.clear_tally_marks()
    state.players[3].is_alive = False
    state._pipeline_runtime.statuses[3] = {CURSE_STATUS}
    assert 3 not in service.tally(window.window_id)


def test_a_curse_cannot_repeat_the_previous_night_and_a_pass_writes_nothing() -> None:
    context = _context(**{"last_cursed": {"seat": 2, "round": 0}})
    context = ActionContext.from_mapping({
        **{field: getattr(context, field) for field in context.__dataclass_fields__},
        "facts": {"last_cursed": {"seat": 2, "round": 0}, "alive_seats": (1, 2)},
        "round_number": 1,
    })
    assert validate_crow_action(
        context, ActionCommand(action_type="curse", target_seat=2, reasoning="又"),
    )
    fresh = ActionContext.from_mapping({
        **{field: getattr(context, field) for field in context.__dataclass_fields__},
        "round_number": 3,
    })
    assert validate_crow_action(
        fresh, ActionCommand(action_type="curse", target_seat=2, reasoning="隔夜"),
    ) == ()
    cursed = resolve_crow_action(
        fresh, ActionCommand(action_type="curse", target_seat=2, reasoning="咒"),
    )
    assert cursed[0].payload["status"] == "cursed"
    assert resolve_crow_action(
        fresh, ActionCommand(action_type="pass", reasoning="不"),
    )[0].payload["payload"]["action_type"] == "pass"


def test_the_link_writes_both_peers_and_the_chain_fires_once() -> None:
    context = _context(alive_seats=(1, 2, 3))
    linked = resolve_cupid_link(context, ActionCommand(
        action_type="link", target_seats=(2, 3), reasoning="连",
    ))
    assert linked[0].payload["value"] == (2, 3)
    assert linked[1].payload["value"]["seat"] == 3
    assert linked[2].payload["value"]["seat"] == 2
    assert resolve_cupid_link(
        context, ActionCommand(action_type="pass", reasoning="不连"),
    )[0].payload["payload"]["action_type"] == "pass"
    chain = _context(
        lover_pair=[2, 3], alive_seats=(1, 3),
        trigger_event={"target_seat": 2},
    )
    chain = ActionContext.from_mapping({
        **{field: getattr(chain, field) for field in chain.__dataclass_fields__},
        "source_event_id": "event:0123456789abcdef",
        "facts": {"lover_pair": [2, 3], "alive_seats": (1, 3)},
        "trigger_event": {"target_seat": 2},
        "resources": {"love_available": 1},
    })
    deaths = react_cupid_chain(chain)
    assert deaths[1].payload["cause"] == "love_death"
    assert react_cupid_chain(ActionContext.from_mapping({
        **{field: getattr(chain, field) for field in chain.__dataclass_fields__},
        "source_event_id": None,
    })) == ()
    assert react_cupid_chain(ActionContext.from_mapping({
        **{field: getattr(chain, field) for field in chain.__dataclass_fields__},
        "facts": {"lover_pair": [2, 3], "alive_seats": ()},
    })) == ()


def test_the_admirer_cannot_pick_themselves_and_a_crush_is_private() -> None:
    context = _context()
    assert validate_admirer_action(
        context, ActionCommand(action_type="crush", target_seat=1, reasoning="自己"),
    )
    assert validate_admirer_action(
        context, ActionCommand(action_type="crush", reasoning="没目标"),
    )
    picked = resolve_admirer_action(
        context, ActionCommand(action_type="crush", target_seat=2, reasoning="他"),
    )
    assert picked[1].payload["key"] == CRUSH_KEY
    assert resolve_admirer_action(
        context, ActionCommand(action_type="pass", reasoning="不"),
    )[0].payload["payload"]["action_type"] == "pass"


def test_reveal_effects_apply_without_taking_the_vote() -> None:
    state = _state((1, "wolf-killer-fool-seer", "good"), (2, "wolf-killer-villager", "good"))
    state._pipeline_runtime.role_resources[1] = {"reveal": 1}
    context = _context(actor_identity={"role_id": "wolf-killer-fool-seer", "camp_id": "good"})
    effects = (
        GameEffect(
            derive_effect_id(context.action_key, 0), EffectKind.ACCEPT_ACTION,
            context.action_key, expected_revision=context.revision, sort_key=(0,),
        ),
        *resolve_reveal_action(
            context, ActionCommand(action_type="reveal", reasoning="亮"),
            event_type="FOOL_SEER_REVEAL_REASONING",
        ),
    )
    EffectApplier().apply(state, effects, EffectPermission(
        1, frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.EMIT_EVENT}),
        frozenset({EffectKind.CONSUME_RESOURCE, EffectKind.EMIT_EVENT}),
        frozenset({1}), frozenset({"PUBLIC", "ACTOR"}),
    ))
    assert state._pipeline_runtime.role_resources[1]["reveal"] == 0
    assert 1 in state.alive_players()


def test_quiet_answers_cover_the_gates_that_a_real_command_skips() -> None:
    context = _context()
    dead = replace(context, actor_alive=False)
    later = replace(context, round_number=2)
    broke = replace(context, resources={})
    assert fool_seer_applicable(context) is True
    assert fool_seer_applicable(dead) is False
    assert crow_applicable(context) is True
    assert crow_applicable(dead) is False
    assert reveal_applicable(context) is True
    assert reveal_applicable(dead) is False
    assert reveal_applicable(broke) is False
    assert admirer_applicable(context) is True
    assert admirer_applicable(dead) is False
    assert admirer_applicable(later) is False
    assert admirer_applicable(broke) is False
    assert cupid_applicable(context) is True
    assert cupid_applicable(dead) is False
    assert cupid_applicable(later) is False
    assert cupid_applicable(broke) is False
    assert chain_applicable(context) is True
    assert chain_applicable(dead) is False
    assert chain_applicable(broke) is False
    passed = ActionCommand(action_type="pass", reasoning="不")
    assert validate_fool_seer_action(context, passed) == ()
    assert validate_crow_action(context, passed) == ()
    assert validate_reveal_action(context, passed) == ()
    assert validate_reveal_action(
        context, ActionCommand(action_type="reveal", reasoning="亮"),
    ) == ()
    assert validate_admirer_action(context, passed) == ()
    assert validate_admirer_action(
        context, ActionCommand(action_type="crush", target_seat=2, reasoning="他"),
    ) == ()
    assert validate_cupid_link(context, passed) == ()
    assert resolve_fool_seer_reveal(context, passed)[0].payload["payload"]["action_type"] == "pass"
    assert resolve_iron_villager_reveal(
        context, ActionCommand(action_type="reveal", reasoning="亮"),
    )[1].payload["event_type"] == "PLAYER_REVEALED"
    chain = replace(context, source_event_id="event:0123456789abcdef", facts={
        "lover_pair": "nope", "alive_seats": (1, 3),
    }, trigger_event={"target_seat": 2})
    assert react_cupid_chain(chain) == ()
    assert react_cupid_chain(replace(chain, resources={})) == ()
    assert react_cupid_chain(replace(
        chain, facts={"lover_pair": [2, 3], "alive_seats": (1, 3)},
        trigger_event={"target_seat": "2"}, resources={"love_available": 1},
    )) == ()
    assert react_cupid_chain(replace(
        chain, facts={"lover_pair": [2, 3], "alive_seats": (1, 3)},
        trigger_event={"target_seat": 9}, resources={"love_available": 1},
    )) == ()
    assert react_cupid_chain(replace(
        chain, facts={"lover_pair": [2, 3], "alive_seats": (1,)},
        trigger_event={"target_seat": 2}, resources={"love_available": 1},
    )) == ()
    assert react_cupid_chain(replace(
        chain, facts={"lover_pair": [2, 3], "alive_seats": "nope"},
        trigger_event={"target_seat": 2}, resources={"love_available": 1},
    )) == ()
