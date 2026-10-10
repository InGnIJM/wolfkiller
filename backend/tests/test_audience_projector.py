"""Audience projection must emit the documented public payload keys."""

from app.models.actions import DeathReport
from app.models.game import GameConfig, GamePhase, GameState, PlayerState
from app.services.audience_projector import AudienceProjector, _EVENTS


def _event(event_type: str, payload: dict, visibility=("PUBLIC",)) -> dict:
    return {
        "event_id": "evt", "event_type": event_type,
        "payload": payload, "visibility": list(visibility),
    }


def test_public_event_catalogue_is_frozen() -> None:
    """The public event types are a contract the frontend renders.

    Adding one without a matching render branch is exactly how an event
    disappears from the stage, so the set is frozen here: update it, then add
    the render case and a row in
    ``frontend/src/components/game/test/eventCoverage.test.tsx``.
    """
    public_types = {definition[0] for definition in _EVENTS.values()}
    assert public_types == {
        "game_initialized", "player_revealed", "execution_state", "phase",
        "speech", "speaking", "death", "vote", "vote_result", "winner",
        "night_action", "night_thought", "technical_abstain",
        "exile_cancelled", "self_explode", "crimson_explode",
        "dance_pool", "mask_granted",
        "knight_duel", "wolf_beauty_charm", "wolf_beauty_revenge",
        "wolf_chat_message", "wolf_vote",
        "sheriff_elected", "sheriff_badge", "sheriff_run",
        "sheriff_withdraw", "sheriff_vote", "sheriff_side",
        "narration", "player_status",
    }


def test_pipeline_death_seat_becomes_player_seat() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("PLAYER_DIED", {"seat": 10, "cause": "wolf_kill", "round_number": 2}),
    ])

    assert projected == [{
        "event_id": "evt:audience",
        "event_type": "death",
        "schema_version": 1,
        "payload": {"player_seat": 10, "cause": "wolf_kill", "round_number": 2},
    }]


def test_death_already_using_player_seat_is_preserved() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("PLAYER_DIED", {"player_seat": 7, "cause": "exile", "round_number": 1}),
    ])

    assert projected[0]["payload"] == {
        "player_seat": 7, "cause": "exile", "round_number": 1,
    }


def test_engine_died_event_target_seat_becomes_player_seat() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("PLAYER_DIED", {"target_seat": 5, "cause": "hunter_shot", "round_number": 3}),
    ])

    assert projected[0]["payload"] == {
        "player_seat": 5, "cause": "hunter_shot", "round_number": 3,
    }


def test_non_public_death_is_not_projected() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("PLAYER_DIED", {"seat": 1, "cause": "wolf_kill", "round_number": 1}, visibility=()),
    ])

    assert projected == []


def test_technical_abstain_player_seat_becomes_voter_seat() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("TECHNICAL_ABSTAIN", {
            "player_seat": 4, "round_number": 2,
            "phase": "vote_casting", "failure_code": "provider_timeout",
        }),
    ])

    assert projected[0]["payload"] == {
        "voter_seat": 4, "round_number": 2, "failure_code": "provider_timeout",
    }


def test_death_without_any_seat_key_is_projected_unchanged() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("PLAYER_DIED", {"cause": "wolf_kill", "round_number": 1}),
    ])

    assert projected[0]["payload"] == {"cause": "wolf_kill", "round_number": 1}


def test_technical_abstain_without_any_seat_key_is_projected_unchanged() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("TECHNICAL_ABSTAIN", {"round_number": 1, "failure_code": "parse_error"}),
    ])

    assert projected[0]["payload"] == {"round_number": 1, "failure_code": "parse_error"}


def test_technical_abstain_already_using_voter_seat_is_preserved() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("TECHNICAL_ABSTAIN", {
            "voter_seat": 6, "round_number": 1, "failure_code": "parse_error",
        }),
    ])

    assert projected[0]["payload"] == {
        "voter_seat": 6, "round_number": 1, "failure_code": "parse_error",
    }


def test_charm_target_reaches_the_god_view() -> None:
    """The audience stream is the god view, so the charmed seat is published.

    Wolf Beauty's charm hides its target from the *players* (the relation stays
    unprojected); the spectator timeline still gets it. Only whitelisted keys
    pass, so a private field a role adds later cannot ride along.
    """
    projected = AudienceProjector().project_events("game", [
        _event("WOLF_BEAUTY_CHARM", {
            "seat": 6, "target_seat": 2, "round_number": 3, "last_charmed": 2,
        }),
    ])

    assert projected[0]["payload"] == {
        "seat": 6, "target_seat": 2, "round_number": 3,
    }


def test_a_narration_keeps_only_its_public_keys() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("NARRATION", {
            "round_number": 2, "title": "天黑请闭眼", "text": "狼人请睁眼",
            "phase": "night", "internal": "x",
        }),
    ])

    assert projected == [{
        "event_id": "evt:audience",
        "event_type": "narration",
        "schema_version": 1,
        "payload": {"round_number": 2, "title": "天黑请闭眼", "text": "狼人请睁眼"},
    }]


def test_the_public_config_carries_every_setting_the_prompts_obey() -> None:
    """A setting the Witch is told to obey must be visible in the god view.

    ``witch_self_save`` is the rule the Witch's own prompt defers to; without it
    in the public config an audience watches the Witch decline a self-save with
    no way to tell whether that was the rule or a choice.
    """
    projected = AudienceProjector().project_events("game", [
        _event("GAME_INITIALIZED", {
            "players": {},
            "config": {
                "role_counts": {"wolf-killer-witch": 1},
                "reveal_on_death": False,
                "enable_sheriff": True,
                "witch_self_save": "never",
                "prompt_digest": "must-not-leak",
            },
        }),
    ])

    assert projected[0]["payload"]["config"] == {
        "role_counts": {"wolf-killer-witch": 1},
        "reveal_on_death": False,
        "enable_sheriff": True,
        "witch_self_save": "never",
    }


def test_a_delayed_death_status_names_its_player_seat() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("STATUS_ADDED", {
            "seat": 5, "status": "delayed_death", "round_number": 2,
            "cause": "poison",
        }),
    ])

    assert projected == [{
        "event_id": "evt:audience",
        "event_type": "player_status",
        "schema_version": 1,
        "payload": {"player_seat": 5, "status": "delayed_death", "round_number": 2},
    }]


def test_a_status_without_a_seat_is_projected_without_one() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("STATUS_ADDED", {"status": "poisoned", "round_number": 1}),
    ])

    assert projected[0]["payload"] == {"status": "poisoned", "round_number": 1}


def test_a_wolf_chat_message_keeps_the_day_plan() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("WOLF_CHAT_MESSAGE", {
            "seat": 4, "text": "今晚刀预言家", "day_plan": "明天推3号",
            "round_number": 1, "channel": "private",
        }),
    ])

    assert projected[0]["payload"] == {
        "seat": 4, "text": "今晚刀预言家", "day_plan": "明天推3号", "round_number": 1,
    }


def test_a_sheriff_badge_keeps_the_sheriff_reason() -> None:
    """警长为什么交徽/撕徽要进上帝视角；未知键仍被剥掉。"""

    projected = AudienceProjector().project_events("game", [
        _event("SHERIFF_BADGE", {
            "from_seat": 1, "to_seat": None, "round_number": 3,
            "reasoning": "9号是金水，但场上已无合适人选", "secret": "x",
        }),
    ])

    assert projected[0]["payload"] == {
        "from_seat": 1, "to_seat": None, "round_number": 3,
        "reasoning": "9号是金水，但场上已无合适人选",
    }


def test_a_status_already_using_player_seat_is_preserved() -> None:
    projected = AudienceProjector().project_events("game", [
        _event("STATUS_ADDED", {"player_seat": 3, "status": "wounded", "round_number": 1}),
    ])

    assert projected[0]["payload"] == {
        "player_seat": 3, "status": "wounded", "round_number": 1,
    }


def _election_state(cause: str = "wolf_kill") -> GameState:
    state = GameState(game_id="g", config=GameConfig(enable_sheriff=True))
    state.players = {
        1: PlayerState(1, "wolf-killer-werewolf", "werewolf", is_alive=False),
        2: PlayerState(2, "wolf-killer-villager", "good"),
    }
    state.phase = GamePhase.SHERIFF_ELECTION
    state.round_number = 1
    state.death_history.append(DeathReport(1, cause, 1))
    return state


def test_the_snapshot_hides_a_death_the_table_has_not_heard_yet() -> None:
    """竞选窗口内首夜死者还没公布，快照必须与事件时间轴同口径。

    快照从前直接读真实 state：观众流的 `death` 事件排在竞选之后，而 `players` 与
    `death_history` 在竞选期间就把他标成已死 —— 官方顺序是「先让警徽落地、再念死讯」，
    这期间他照常上警、发言、投票。
    """
    snapshot = AudienceProjector().snapshot(_election_state(), execution_status="running")

    assert snapshot["players"]["1"]["is_alive"] is True
    assert snapshot["death_history"] == []


def test_the_snapshot_shows_the_same_death_once_it_is_announced() -> None:
    """竞选结束后（phase=dawn）同一份 state 必须如实显示死亡。"""
    state = _election_state()
    state.phase = GamePhase.DAWN

    snapshot = AudienceProjector().snapshot(state, execution_status="running")

    assert snapshot["players"]["1"]["is_alive"] is False
    assert snapshot["death_history"] == [
        {"player_seat": 1, "cause": "wolf_kill", "round_number": 1},
    ]


def test_the_snapshot_keeps_an_immediately_announced_death() -> None:
    """竞选期自爆是当场公布的死因，不能被「未公布」投影复活。"""
    snapshot = AudienceProjector().snapshot(
        _election_state(cause="self_explode"), execution_status="running",
    )

    assert snapshot["players"]["1"]["is_alive"] is False
    assert snapshot["death_history"] == [
        {"player_seat": 1, "cause": "self_explode", "round_number": 1},
    ]
