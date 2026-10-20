"""Whitelist projection from internal domain events to the audience stream."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from app.core.sheriff_flow import unannounced_death_seats
from app.models.game import GameState


_EVENTS: dict[str, tuple[str, frozenset[str]]] = {
    "GAME_INITIALIZED": ("game_initialized", frozenset({"players", "config"})),
    "PLAYER_REVEALED": ("player_revealed", frozenset({"seat_number", "role", "camp"})),
    "EXECUTION_STATE": ("execution_state", frozenset({"execution_status", "recoverable", "recovery_block_code"})),
    "PHASE_CHANGED": ("phase", frozenset({"phase", "round_number"})),
    "NARRATION": ("narration", frozenset({"round_number", "title", "text"})),
    # A settlement that spares a delayable seat leaves the marks that explain
    # the pending death; without this type the god view saw the seat survive a
    # poison or a shot with no reason given.
    "STATUS_ADDED": ("player_status", frozenset({"player_seat", "seat", "status", "round_number"})),
    "SPEECH_MADE": ("speech", frozenset({"player_seat", "text", "round_number", "phase"})),
    "SPEAKING": ("speaking", frozenset({"seat", "round_number"})),
    "PLAYER_DIED": ("death", frozenset({"player_seat", "seat", "target_seat", "cause", "round_number"})),
    "VOTE_CAST": ("vote", frozenset({"voter_seat", "target_seat", "round_number", "status"})),
    # `weighted_counts`（半票加权计票）与 `counts`（人头数）并列发布：放逐结果按
    # `ballot_weight` 判定，只给未加权的人头数会让「谁出局」在票型旁边读不通
    # （警长 1.5 票对人 1 票看起来是平局）。白名单漏掉它时，域事件带了、
    # 观众流与回放都没有 —— `game_routes._AUDIENCE_ACTION_OPTIONAL` 也就成了死配置。
    "VOTE_RESULT": ("vote_result", frozenset({"round_number", "exiled_seat", "counts", "weighted_counts"})),
    # `winners`（按座位列出胜者）只在「胜负不等于阵营」的板子出现（孤独少女随
    # 崇拜对象的阵营胜负），老对局的 `win_result` 没有这个键，所以它是可选字段。
    "GAME_OVER": ("winner", frozenset({"winning_camp", "reason", "winners"})),
    "NIGHT_ACTION": ("night_action", frozenset({
        "action_type", "target_seat", "round_number", "vote_counts", "result",
        "seat", "intended_seat",
    })),
    # 定序王子把当天第一次放逐投票作废：公开这条，前端才不会把随后的
    # ``exiled_seat is None`` 票型读成平票。
    "VOTE_REWOUND": ("vote_rewound", frozenset({"seat", "round_number"})),
    "TECHNICAL_ABSTAIN": ("technical_abstain", frozenset({"player_seat", "voter_seat", "round_number", "failure_code"})),
    "EXILE_CANCELLED": ("exile_cancelled", frozenset({"target_seat", "round_number"})),
    "SELF_EXPLODE": ("self_explode", frozenset({"seat", "target_seat", "round_number"})),
    "SHERIFF_ELECTED": ("sheriff_elected", frozenset({"seat", "round_number", "reason"})),
    "SHERIFF_BADGE": ("sheriff_badge", frozenset({"from_seat", "to_seat", "round_number", "reasoning"})),
    "SHERIFF_RUN": ("sheriff_run", frozenset({"seat", "choice", "round_number"})),
    "SHERIFF_WITHDRAW": ("sheriff_withdraw", frozenset({"seat", "choice", "round_number"})),
    "SHERIFF_VOTE": ("sheriff_vote", frozenset({"voter_seat", "target_seat", "kind", "round_number"})),
    "SHERIFF_SIDE": ("sheriff_side", frozenset({"seat", "side", "round_number"})),
    # The judge announces the duel verdict publicly, so the audience sees the
    # challenged camp; the charm publishes its target seat because this stream
    # is the god view (the players' own view never receives either event).
    "KNIGHT_DUEL": ("knight_duel", frozenset({"seat", "target_seat", "camp", "round_number"})),
    "WOLF_BEAUTY_CHARM": ("wolf_beauty_charm", frozenset({"seat", "target_seat", "round_number"})),
    # 觉醒狼美人的挽歌幻象：观众需要看到她把谁当作替身（本人提示词与公开记录都不带）。
    "WRAITH_ILLUSION": ("wraith_illusion", frozenset({"seat", "target_seat", "round_number"})),
    "WOLF_BEAUTY_REVENGE": ("wolf_beauty_revenge", frozenset({"seat", "target_seat", "cause", "round_number"})),
    # The Crimson Envoy takes nobody along: what the god view needs is which
    # seats it sealed for the coming night.
    "CRIMSON_EXPLODE": ("crimson_explode", frozenset({"seat", "silenced_seats", "round_number"})),
    # 寻香魅影的绑定：被绑的两人并不知道自己被绑，只有上帝视角看得到这一拍。
    "FRAGRANCE_BOND": ("fragrance_bond", frozenset({"seat", "target_seats", "round_number"})),
    # The Masquerade's two nightly beats. The pool names the three seats so the
    # god view can follow the verdict; the mask names its target and whether it
    # landed inside the pool.
    "DANCE_POOL": ("dance_pool", frozenset({"seat", "pool_seats", "round_number"})),
    "MASK_GRANTED": ("mask_granted", frozenset({"seat", "target_seat", "in_pool", "round_number"})),
}

_PUBLIC_ROLE_ACTIONS = {
    "WEREWOLF_KILL": "werewolf_kill",
    "WITCH_SAVE": "witch_save",
    "WITCH_POISON": "witch_poison",
    "SEER_CHECK": "seer_check",
    "FOOL_SEER_CHECK": "fool_seer_check",
    "HUNTER_SHOT": "hunter_shot",
    "AWAKENED_HUNT": "awakened_hunt",
    "WOLF_KING_SHOT": "wolf_king_shot",
    "GUARD_PROTECT": "guard_protect",
}
_PUBLIC_REASONING_EVENTS = {
    "HUNTER_REASONING": "hunter_reasoning",
    "WOLF_KING_REASONING": "wolf_king_reasoning",
    "DREAM_WEAVER_REASONING": "dream_weaver_reasoning",
    "GARGOYLE_REASONING": "gargoyle_reasoning",
    "WITCH_REASONING": "witch_reasoning",
    "SEER_REASONING": "seer_reasoning",
    "GUARD_REASONING": "guard_reasoning",
    "WEREWOLF_KING_REASONING": "werewolf_king_reasoning",
    "KNIGHT_REASONING": "knight_reasoning",
    "WOLF_BEAUTY_REASONING": "wolf_beauty_reasoning",
    "PURE_WHITE_REASONING": "pure_white_reasoning",
    "WOLF_WITCH_REASONING": "wolf_witch_reasoning",
    "DEMON_HUNTER_REASONING": "demon_hunter_reasoning",
    "CRIMSON_ENVOY_REASONING": "crimson_envoy_reasoning",
    "NIGHTMARE_REASONING": "nightmare_reasoning",
    "DANCER_REASONING": "dancer_reasoning",
    "MASK_REASONING": "mask_reasoning",
    "AWAKENED_HUNTER_REASONING": "awakened_hunter_reasoning",
    "AWAKENED_BEAUTY_REASONING": "awakened_beauty_reasoning",
    "AWAKENED_SEER_REASONING": "awakened_seer_reasoning",
    "FRAGRANCE_PHANTOM_REASONING": "fragrance_phantom_reasoning",
    "MIRACLE_MERCHANT_REASONING": "miracle_merchant_reasoning",
    "LUCKY_REASONING": "lucky_reasoning",
    "MIRROR_GIRL_REASONING": "mirror_girl_reasoning",
    "AWAKENED_HIDDEN_WOLF_REASONING": "awakened_hidden_wolf_reasoning",
    "RADIANCE_EARL_REASONING": "radiance_earl_reasoning",
    "ECLIPSE_MAID_REASONING": "eclipse_maid_reasoning",
    "TIME_WOLF_REASONING": "time_wolf_reasoning",
    "ORDER_PRINCE_REASONING": "order_prince_reasoning",
    "FOOL_SEER_REASONING": "fool_seer_reasoning",
    "FOOL_SEER_REVEAL_REASONING": "fool_seer_reveal_reasoning",
    "IRON_VILLAGER_REASONING": "iron_villager_reasoning",
    "CROW_REASONING": "crow_reasoning",
    "CUPID_REASONING": "cupid_reasoning",
    "ADMIRER_REASONING": "admirer_reasoning",
}
_PUBLIC_PLAYER_FIELDS = frozenset({
    "seat_number", "is_alive", "is_sheriff", "role", "camp", "revealed_role",
})
#: Config keys the god view may see. ``witch_self_save`` belongs here because the
#: Witch's own prompt tells it to obey that setting: an audience that cannot see
#: it watches the Witch decline a self-save with no way to tell why.
_PUBLIC_CONFIG_FIELDS = frozenset({
    "role_counts", "reveal_on_death", "enable_sheriff", "witch_self_save", "victory",
})
for _domain_type in _PUBLIC_ROLE_ACTIONS:
    _EVENTS[_domain_type] = (
        "night_action",
        frozenset({
            "target_seat", "round_number", "vote_counts", "result",
            "seat", "intended_seat",
        }),
    )
_EVENTS["WOLF_CHAT_MESSAGE"] = (
    "wolf_chat_message", frozenset({"seat", "text", "day_plan", "round_number"}),
)
_EVENTS["WOLF_VOTE"] = (
    "wolf_vote", frozenset({"seat", "target_seat", "reasoning", "round_number", "degraded"}),
)
for _domain_type in _PUBLIC_REASONING_EVENTS:
    _EVENTS[_domain_type] = (
        "night_thought",
        frozenset({"seat", "action_type", "target_seat", "reasoning", "round_number", "degraded"}),
    )


class AudienceProjector:
    projection_version = 1

    def project_events(
        self, game_id: str, events: Iterable[Mapping[str, object]],
    ) -> list[dict[str, object]]:
        projected: list[dict[str, object]] = []
        for event in events:
            event_type = event.get("event_type")
            definition = _EVENTS.get(event_type) if isinstance(event_type, str) else None
            visibility = event.get("visibility", ())
            if definition is None or not isinstance(visibility, (list, tuple, set, frozenset)) or "PUBLIC" not in visibility:
                continue
            event_id = event.get("event_id")
            payload = event.get("payload")
            if not isinstance(event_id, str) or not event_id or not isinstance(payload, Mapping):
                raise ValueError("public domain event is missing event_id or payload")
            public_type, allowed = definition
            public_payload: dict[str, object] = {
                key: value for key, value in payload.items() if key in allowed
            }
            if event_type == "PLAYER_DIED":
                # Emitters disagree on the seat key (pipeline uses ``seat``,
                # the engine lifecycle fact uses ``target_seat``); the public
                # contract is always ``player_seat``.
                seat = public_payload.get("player_seat")
                if seat is None:
                    seat = public_payload.get("seat")
                if seat is None:
                    seat = public_payload.get("target_seat")
                for key in ("player_seat", "seat", "target_seat"):
                    public_payload.pop(key, None)
                if seat is not None:
                    public_payload["player_seat"] = seat
            if event_type == "STATUS_ADDED":
                # The settlement names the seat ``seat``; the public contract
                # keeps the same ``player_seat`` shape a death uses.
                seat = public_payload.get("player_seat")
                if seat is None:
                    seat = public_payload.get("seat")
                for key in ("player_seat", "seat"):
                    public_payload.pop(key, None)
                if seat is not None:
                    public_payload["player_seat"] = seat
            if event_type == "TECHNICAL_ABSTAIN":
                seat = public_payload.get("voter_seat")
                if seat is None:
                    seat = public_payload.get("player_seat")
                public_payload.pop("player_seat", None)
                public_payload.pop("voter_seat", None)
                if seat is not None:
                    public_payload["voter_seat"] = seat
            if event_type == "GAME_INITIALIZED":
                players = payload.get("players")
                if isinstance(players, Mapping):
                    public_payload["players"] = {
                        str(seat): {
                            key: value for key, value in player.items()
                            if key in _PUBLIC_PLAYER_FIELDS
                        }
                        for seat, player in players.items()
                        if isinstance(player, Mapping)
                    }
                elif isinstance(players, list):
                    public_payload["players"] = [{
                        key: value for key, value in player.items()
                        if key in _PUBLIC_PLAYER_FIELDS
                    } for player in players if isinstance(player, Mapping)]
                config = payload.get("config")
                if isinstance(config, Mapping):
                    public_payload["config"] = {
                        key: value for key, value in config.items()
                        if key in _PUBLIC_CONFIG_FIELDS
                    }
            action_type = _PUBLIC_ROLE_ACTIONS.get(event_type)
            if action_type is not None:
                public_payload = {"action_type": action_type, **public_payload}
            reasoning_type = _PUBLIC_REASONING_EVENTS.get(event_type)
            if reasoning_type is not None:
                public_payload["action_type"] = reasoning_type
            row: dict[str, object] = {
                "event_id": f"{event_id}:audience",
                "event_type": public_type,
                "schema_version": 1,
                "payload": public_payload,
            }
            timestamp = event.get("timestamp")
            if isinstance(timestamp, str) and timestamp:
                row["timestamp"] = timestamp
            projected.append(row)
        return projected

    def snapshot(self, state: GameState, *, execution_status: str,
                 recovery_block_code: str | None = None) -> dict[str, object]:
        # The first night settles before the election but is announced after it,
        # so for the length of that window the table has not heard the deaths
        # yet: the snapshot must agree with the event timeline it accompanies.
        # A seat whose death is unannounced is still playing (it campaigns,
        # speaks and votes), and its death row must not appear in the god view's
        # death list either. Outside the window the set is empty and nothing
        # moves. The election's own self-explode is announced where it happens,
        # so it is never in this set.
        unannounced = unannounced_death_seats(state)
        return {
            "game_id": state.game_id,
            "phase": state.phase.value,
            "round_number": state.round_number,
            "execution_status": execution_status,
            "recoverable": execution_status in {"paused", "interrupted"} and recovery_block_code is None,
            "recovery_block_code": recovery_block_code,
            "players": {
                str(seat): {
                    "seat_number": player.seat_number,
                    "is_alive": player.is_alive or seat in unannounced,
                    "is_sheriff": player.is_sheriff,
                    "role": player.role,
                    "camp": player.camp,
                    "revealed_role": player.revealed_role,
                }
                for seat, player in sorted(state.players.items())
            },
            "sheriff": state.sheriff,
            "speeches": [item.to_dict() for item in state.speeches],
            "votes": [item.to_dict() for item in state.votes],
            "death_history": [
                item.to_dict() for item in state.death_history
                if item.player_seat not in unannounced
            ],
            "win_result": state.win_result,
        }
