"""Project mutable game state into a minimal, deeply frozen action context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from app.models.actions import SpeechRecord, VoteAction
from app.models.game import GameState, PlayerState
from app.models.pipeline import ActionContext, IssuedActionRequest
from app.roles.registry import RegistrySnapshot


_KNOWN_NAMESPACES = frozenset({"PUBLIC", "ACTOR", "CAMP", "RELATION"})
_TRIGGER_EVENT_FIELDS = frozenset(
    {
        "event_id",
        "type",
        "source_seat",
        "target_seat",
        "cause",
        "round_number",
        "phase",
    }
)
_AGGREGATE_RESULT_FIELDS = frozenset(
    {"contract_id", "action_type", "target_seat", "count", "tied", "selected_seat"}
)
_ACCEPTED_SUMMARY_FIELDS = frozenset(
    {"actor_seat", "contract_id", "action_type", "target_seat"}
)
_PUBLIC_TRIGGER_REASONS = frozenset(
    {"wolf_kill", "poison", "hunter_shot", "exile", "self_explode", "love_death"}
)
_PUBLIC_EVENT_TYPES = frozenset({"PLAYER_DIED"})
_PUBLIC_PHASES = frozenset(
    {
        "waiting", "role_deal", "night", "dawn", "last_words",
        "sheriff_election", "speech", "vote_casting", "vote_resolution",
        "game_over",
    }
)
_CAMPS = frozenset({"good", "werewolf", "third_party"})
_RESOURCE_ADAPTERS = {
    "antidote": "has_antidote", "has_antidote": "has_antidote",
    "poison": "has_poison", "has_poison": "has_poison",
    "gun": "has_gun", "has_gun": "has_gun",
}
_SPEECH_FIELDS = ("player_seat", "text", "round_number")
_VOTE_FIELDS = ("voter_seat", "target_seat", "round_number")


class ContextProjector:
    """Create the only state snapshot that pipeline hooks and prompts may read."""

    def project(
        self,
        state: GameState,
        request: IssuedActionRequest,
        registry: RegistrySnapshot,
        *,
        source_event_id: str | None = None,
        trigger_event: Mapping[str, object] | None = None,
        trigger_reason: str | None = None,
        accepted_command_summaries: Sequence[object] = (),
        aggregate_result: Mapping[str, object] | None = None,
        counters: Mapping[str, int] | None = None,
    ) -> ActionContext:
        self._validate_boundaries(state, request, registry)
        spec = registry.require(request.role_id)
        actor = self._validate_request(state, request, spec.camp_id, spec.contracts)

        declared = spec.visibility_namespaces | request.contract.visibility_namespaces
        unknown = declared - _KNOWN_NAMESPACES
        if unknown:
            raise ValueError(f"unknown visibility namespace: {min(unknown)}")
        visible = spec.visibility_namespaces & request.contract.visibility_namespaces

        facts = self._public_facts(state)
        resources: dict[str, object] = {}
        if "ACTOR" in visible:
            facts["actor_identity"] = {
                "seat": actor.seat_number,
                "role_id": request.role_id,
                "camp_id": spec.camp_id,
            }
            resources = self._actor_resources(actor, spec.initial_resources)
            facts.update(
                self._actor_private_facts(
                    state,
                    actor,
                    spec.initial_private_data,
                    resources,
                )
            )
        if "CAMP" in visible:
            # Known identities persist after death; only public life state changes.
            facts["camp_members"] = tuple(
                sorted(
                    seat
                    for seat, player in state.players.items()
                    if player.camp == actor.camp
                )
            )
        # RELATION is intentionally a no-op until GameState has tagged relation facts.

        # Until Task 19 adds GameState.state_revision, the signed request is the
        # only authoritative revision boundary and must be copied exactly.
        return ActionContext(
            game_id=state.game_id,
            revision=request.context_revision,
            config_version=registry.digest,
            round_number=request.round_number,
            phase=request.phase,
            window_id=request.window_id,
            schedule_point=request.contract.schedule_point,
            actor_seat=request.actor_seat,
            actor_role_id=request.role_id,
            actor_alive=actor.is_alive,
            resources=resources,
            action_key=request.action_key,
            counters={} if counters is None else dict(counters),
            source_event_id=self._optional_text(source_event_id, "source_event_id", 128),
            trigger_event=self._project_trigger_event(trigger_event, request.contract),
            trigger_reason=self._validate_trigger_reason(
                trigger_reason, request.contract.response_reasons
            ),
            accepted_command_summaries=tuple(
                self._project_command_result(
                    summary, request.contract, "accepted command summary", False
                )
                for summary in accepted_command_summaries
            ),
            aggregate_result=(
                None if aggregate_result is None else self._project_command_result(
                    aggregate_result, request.contract, "aggregate_result", True
                )
            ),
            facts=facts,
        )

    @staticmethod
    def _validate_boundaries(
        state: object, request: object, registry: object
    ) -> None:
        if type(state) is not GameState:
            raise TypeError("state must be a GameState")
        if type(request) is not IssuedActionRequest:
            raise TypeError("request must be an IssuedActionRequest")
        if type(registry) is not RegistrySnapshot:
            raise TypeError("registry must be a RegistrySnapshot")

    @staticmethod
    def _validate_request(
        state: GameState,
        request: IssuedActionRequest,
        camp_id: str,
        contracts: tuple[object, ...],
    ) -> PlayerState:
        actor = state.players.get(request.actor_seat)
        if actor is None:
            raise ValueError("actor seat does not exist in state")
        if actor.seat_number != request.actor_seat:
            raise ValueError("actor seat number does not match request")
        if actor.role != request.role_id:
            raise ValueError("actor role does not match state")
        if actor.camp != camp_id:
            raise ValueError("actor camp does not match registered role")
        state_phase = state.phase.value if hasattr(state.phase, "value") else state.phase
        if state_phase != request.phase:
            raise ValueError("request phase does not match state")
        if state.round_number != request.round_number:
            raise ValueError("request round does not match state")
        if request.context_revision < 0:
            raise ValueError("context revision must be non-negative")
        if request.contract not in contracts:
            raise ValueError("request contract is not registered for actor role")
        return actor

    @classmethod
    def _public_facts(cls, state: GameState) -> dict[str, object]:
        return {
            "alive_seats": tuple(
                sorted(seat for seat, player in state.players.items() if player.is_alive)
            ),
            "dead_seats": tuple(
                sorted(
                    seat for seat, player in state.players.items() if not player.is_alive
                )
            ),
            "sheriff": state.sheriff,
            "phase": state.phase.value if hasattr(state.phase, "value") else state.phase,
            "round_number": state.round_number,
            "speeches": tuple(
                projected
                for record in state.speeches[-20:]
                if (projected := cls._project_speech(record)) is not None
            ),
            "votes": tuple(
                projected
                for record in state.votes[-20:]
                if (projected := cls._project_vote(record)) is not None
            ),
        }

    @classmethod
    def _project_speech(cls, record: object) -> dict[str, object] | None:
        if type(record) is SpeechRecord:
            raw: Mapping[str, object] = {
                "player_seat": record.player_seat,
                "text": record.text,
                "round_number": record.round_number,
            }
        elif isinstance(record, Mapping):
            raw = record
        else:
            return None
        try:
            return {
                "player_seat": cls._positive_int(raw.get("player_seat"), "player_seat"),
                "text": cls._text(raw.get("text"), "speech text", 2000),
                "round_number": cls._nonnegative_int(
                    raw.get("round_number"), "round_number"
                ),
            }
        except (TypeError, ValueError):
            return None

    @classmethod
    def _project_vote(cls, record: object) -> dict[str, object] | None:
        if type(record) is VoteAction:
            raw: Mapping[str, object] = {
                "voter_seat": record.voter_seat,
                "target_seat": record.target_seat,
            }
        elif isinstance(record, Mapping):
            raw = record
        else:
            return None
        try:
            projected: dict[str, object] = {
                "voter_seat": cls._positive_int(raw.get("voter_seat"), "voter_seat"),
                "target_seat": cls._optional_positive_int(
                    raw.get("target_seat"), "target_seat"
                ),
            }
            if "round_number" in raw:
                projected["round_number"] = cls._nonnegative_int(
                    raw["round_number"], "round_number"
                )
            return projected
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _actor_resources(
        actor: PlayerState, declarations: Mapping[str, object]
    ) -> dict[str, object]:
        resources: dict[str, object] = {}
        for key, default in declarations.items():
            attribute = _RESOURCE_ADAPTERS.get(key)
            resources[key] = (
                getattr(actor, attribute)
                if attribute is not None
                else ContextProjector._copy_json_value(default)
            )
        return resources

    @classmethod
    def _actor_private_facts(
        cls,
        state: GameState,
        actor: PlayerState,
        declarations: Mapping[str, object],
        resources: Mapping[str, object],
    ) -> dict[str, object]:
        facts: dict[str, object] = {}
        for key, default in declarations.items():
            if key in {"private_checks", "check_results"}:
                facts["private_checks"] = tuple(
                    projected
                    for result in actor.check_results[-20:]
                    if (projected := cls._project_check_result(result)) is not None
                )
            elif key in {"wolf_kill_target", "last_wolf_kill_target"}:
                has_antidote = bool(
                    resources.get("antidote", resources.get("has_antidote", False))
                )
                if has_antidote:
                    facts["wolf_kill_target"] = state.last_wolf_kill_target
            else:
                facts[key] = cls._copy_json_value(default)
        return facts

    @staticmethod
    def _project_check_result(result: object) -> dict[str, object] | None:
        if not isinstance(result, Mapping):
            return None
        target = result.get("target", result.get("target_seat"))
        camp = result.get("camp", result.get("result"))
        if type(target) is not int or target <= 0 or camp not in _CAMPS:
            return None
        return {"target": target, "camp": camp}

    @classmethod
    def _project_trigger_event(
        cls, value: Mapping[str, object] | None, contract: object
    ) -> dict[str, object] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise TypeError("trigger_event must be a mapping")
        projected: dict[str, object] = {}
        if "event_id" in value:
            projected["event_id"] = cls._text(value["event_id"], "event_id", 128)
        if "type" in value:
            event_type = cls._text(value["type"], "event type", 64)
            allowed = contract.response_event_types or _PUBLIC_EVENT_TYPES
            if event_type not in allowed:
                raise ValueError("unknown event type")
            projected["type"] = event_type
        for key in ("source_seat", "target_seat"):
            if key in value:
                projected[key] = cls._positive_int(value[key], key)
        if "cause" in value:
            cause = cls._text(value["cause"], "cause", 32)
            allowed_reasons = contract.response_reasons or _PUBLIC_TRIGGER_REASONS
            if cause not in allowed_reasons:
                raise ValueError("unknown cause")
            projected["cause"] = cause
        if "round_number" in value:
            projected["round_number"] = cls._nonnegative_int(
                value["round_number"], "round_number"
            )
        if "phase" in value:
            phase = cls._text(value["phase"], "phase", 32)
            if phase not in _PUBLIC_PHASES:
                raise ValueError("unknown phase")
            projected["phase"] = phase
        return projected

    @classmethod
    def _project_command_result(
        cls, value: object, contract: object, name: str, aggregate: bool
    ) -> dict[str, object]:
        if not isinstance(value, Mapping):
            raise TypeError(f"{name} must be a mapping")
        allowed = _AGGREGATE_RESULT_FIELDS if aggregate else _ACCEPTED_SUMMARY_FIELDS
        projected: dict[str, object] = {}
        if "actor_seat" in allowed and "actor_seat" in value:
            projected["actor_seat"] = cls._positive_int(
                value["actor_seat"], "actor_seat"
            )
        if "contract_id" in value:
            contract_id = cls._text(value["contract_id"], "contract_id", 128)
            if contract_id != contract.contract_id:
                raise ValueError("contract_id does not match request contract")
            projected["contract_id"] = contract_id
        if "action_type" in value:
            action_type = cls._text(value["action_type"], "action_type", 64)
            if action_type not in contract.action_types:
                raise ValueError("action_type is not allowed by request contract")
            projected["action_type"] = action_type
        if "target_seat" in value:
            projected["target_seat"] = cls._optional_positive_int(
                value["target_seat"], "target_seat"
            )
        if aggregate and "count" in value:
            projected["count"] = cls._nonnegative_int(value["count"], "count")
        if aggregate and "tied" in value:
            if type(value["tied"]) is not bool:
                raise TypeError("tied must be a boolean")
            projected["tied"] = value["tied"]
        if aggregate and "selected_seat" in value:
            projected["selected_seat"] = cls._optional_positive_int(
                value["selected_seat"], "selected_seat"
            )
        return projected

    @classmethod
    def _validate_trigger_reason(
        cls, reason: str | None, declared: frozenset[str]
    ) -> str | None:
        if reason is None:
            return None
        reason = cls._text(reason, "trigger reason", 32)
        if reason not in (declared or _PUBLIC_TRIGGER_REASONS):
            raise ValueError("unknown public trigger reason")
        return reason

    @staticmethod
    def _text(value: object, name: str, limit: int) -> str:
        if type(value) is not str:
            raise TypeError(f"{name} must be a string")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError(f"{name} must be valid UTF-8") from error
        if len(value) > limit:
            raise ValueError(f"{name} is too long")
        return value

    @classmethod
    def _optional_text(cls, value: object, name: str, limit: int) -> str | None:
        return None if value is None else cls._text(value, name, limit)

    @staticmethod
    def _positive_int(value: object, name: str) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        return value

    @classmethod
    def _optional_positive_int(cls, value: object, name: str) -> int | None:
        return None if value is None else cls._positive_int(value, name)

    @staticmethod
    def _nonnegative_int(value: object, name: str) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        return value

    @classmethod
    def _copy_json_value(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {key: cls._copy_json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return tuple(cls._copy_json_value(item) for item in value)
        return value
