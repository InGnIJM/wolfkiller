"""Project mutable game state into a minimal, deeply frozen action context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from types import MappingProxyType
from app.models.actions import SpeechRecord, VoteAction
from app.models.game import GameState, PlayerState
from app.models.pipeline import (
    ActionCommand, ActionContext, IssuedActionRequest, SchedulePoint,
)
from app.roles.registry import RegistrySnapshot
from app.core.role_runtime import (
    role_action_counters, role_private_data_view, role_private_facts_view,
    role_resource_view,
)


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
_PUBLIC_PHASES = frozenset(
    {
        "waiting", "role_deal", "night", "dawn", "last_words",
        "sheriff_election", "speech", "vote_casting", "vote_resolution",
        "game_over",
    }
)
_CAMPS = frozenset({"good", "werewolf", "third_party"})
_STABLE_TOKEN = re.compile(r"^[a-z][a-z0-9_.-]*$")
_EVENT_TOKEN = re.compile(r"^(?:[a-z][a-z0-9_.-]*|[A-Z][A-Z0-9_]*)$")
_OPAQUE_EVENT_ID = re.compile(r"^event:[0-9a-f]{16,64}$")
_MAX_INT = 2_147_483_647
_MAX_SUMMARIES = 64
_RESOURCE_ADAPTERS = {
    "antidote": "has_antidote", "has_antidote": "has_antidote",
    "poison": "has_poison", "has_poison": "has_poison",
    "gun": "has_gun", "has_gun": "has_gun",
}
_SPEECH_FIELDS = ("player_seat", "text", "round_number")
_VOTE_FIELDS = ("voter_seat", "target_seat", "round_number")


class ContextProjector:
    """Create the only state snapshot that pipeline hooks and prompts may read."""

    def project_selected_target(
        self, state: GameState, request: IssuedActionRequest,
        context: ActionContext, command: ActionCommand,
        registry: RegistrySnapshot,
    ) -> ActionContext:
        self._validate_boundaries(state, request, registry)
        if type(context) is not ActionContext:
            raise TypeError("context must be an ActionContext")
        if type(command) is not ActionCommand:
            raise TypeError("command must be an ActionCommand")
        spec = registry.require(request.role_id)
        self._validate_request(state, request, spec.camp_id, spec.contracts)
        expected = (
            (context.game_id, state.game_id),
            (context.revision, request.context_revision),
            (context.actor_seat, request.actor_seat),
            (context.actor_role_id, request.role_id),
            (context.contract_id, request.contract.contract_id),
            (context.contract_version, request.contract.schema_version),
            (context.contract_digest, request.contract.stable_digest()),
            (context.action_key, request.action_key),
            (context.window_id, request.window_id),
            (context.round_number, request.round_number),
            (context.phase, request.phase),
            (context.schedule_point, request.contract.schedule_point),
            (context.config_version, registry.digest),
        )
        if any(actual != bound for actual, bound in expected):
            raise ValueError("context does not match request and registry")
        namespaces = request.contract.selected_target_fact_namespaces
        if not namespaces or command.target_seat is None:
            return context
        if namespaces != frozenset({"camp_label"}):
            raise ValueError("unknown selected target fact namespace")
        target = state.players.get(command.target_seat)
        if target is None or not target.is_alive:
            return context
        camp = self._token(target.camp, "selected target camp", 128)
        facts = dict(context.facts)
        facts["selected_target"] = {"seat": command.target_seat, "camp_label": camp}
        return ActionContext.from_mapping({
            field: getattr(context, field)
            for field in context.__dataclass_fields__
        } | {"facts": facts})

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
        self._validate_response_inputs(
            request,
            source_event_id,
            trigger_event,
            trigger_reason,
            accepted_command_summaries,
        )

        declared = spec.visibility_namespaces | request.contract.visibility_namespaces
        unknown = declared - _KNOWN_NAMESPACES
        if unknown:
            raise ValueError(f"unknown visibility namespace: {min(unknown)}")
        visible = spec.visibility_namespaces & request.contract.visibility_namespaces

        facts = self._public_facts(state, registry)
        resources: dict[str, object] = {}
        if "ACTOR" in visible:
            facts["actor_identity"] = {
                "seat": actor.seat_number,
                "role_id": request.role_id,
                "camp_id": spec.camp_id,
            }
            resources = self._actor_resources(state, actor, spec.initial_resources)
            facts.update(
                self._actor_private_facts(
                    state,
                    actor,
                    spec.initial_private_data,
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
            contract_id=request.contract.contract_id,
            contract_version=request.contract.schema_version,
            contract_digest=request.contract.stable_digest(),
            round_number=request.round_number,
            phase=request.phase,
            window_id=request.window_id,
            schedule_point=request.contract.schedule_point,
            actor_seat=request.actor_seat,
            actor_role_id=request.role_id,
            actor_alive=actor.is_alive,
            resources=resources,
            action_key=request.action_key,
            counters=self._project_counters(dict(role_action_counters(
                state, request.actor_seat, request.contract.contract_id,
                request.window_id, request.round_number,
            )) if counters is None else counters),
            source_event_id=self._optional_event_id(source_event_id, "source_event_id"),
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

    def project_view(
        self, state: GameState, registry: RegistrySnapshot, seat: int
    ) -> ActionContext:
        """Project a contract-free observation context for one seat.

        Mirrors `project` for a synthetic observation boundary so non-action
        flows (day speech, votes, audience views) share the same frozen,
        namespace-filtered facts as pipeline actions without issuing a
        registered contract.
        """
        if type(state) is not GameState:
            raise TypeError("state must be a GameState")
        if type(registry) is not RegistrySnapshot:
            raise TypeError("registry must be a RegistrySnapshot")
        if type(seat) is not int or seat <= 0:
            raise ValueError("invalid view seat")
        actor = state.players.get(seat)
        if actor is None:
            raise ValueError("actor seat does not exist in state")
        spec = registry.require(actor.role)
        if actor.camp != spec.camp_id:
            raise ValueError("actor camp does not match registered role")
        facts = self._public_facts(state, registry)
        facts["actor_identity"] = {
            "seat": actor.seat_number,
            "role_id": actor.role,
            "camp_id": spec.camp_id,
        }
        if "CAMP" in spec.visibility_namespaces:
            facts["camp_members"] = tuple(
                sorted(
                    seat
                    for seat, player in state.players.items()
                    if player.camp == actor.camp
                )
            )
        resources = self._actor_resources(state, actor, spec.initial_resources)
        facts.update(
            self._actor_private_facts(
                state, actor, spec.initial_private_data
            )
        )
        return ActionContext(
            game_id=state.game_id,
            revision=0,
            facts=facts,
            config_version=registry.digest,
            contract_id="observation",
            contract_version=1,
            contract_digest="observation",
            round_number=state.round_number,
            phase=state.phase.value if hasattr(state.phase, "value") else state.phase,
            window_id="observation",
            schedule_point=SchedulePoint.DAY_ACTION,
            actor_seat=actor.seat_number,
            actor_role_id=actor.role,
            actor_alive=actor.is_alive,
            resources=resources,
            action_key="observation",
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
    def _validate_response_inputs(
        request: IssuedActionRequest,
        source_event_id: object,
        trigger_event: object,
        trigger_reason: object,
        summaries: Sequence[object],
    ) -> None:
        if trigger_event is not None and type(trigger_event) is not dict:
            raise TypeError("trigger_event must be an exact dict")
        if (source_event_id is None) != (trigger_event is None):
            raise ValueError("source_event_id and trigger_event must be paired")
        declared_events = request.contract.response_event_types
        if (source_event_id is not None or trigger_event is not None) and not declared_events:
            raise ValueError("response event payload requires a declared response event")
        if trigger_reason is not None and not request.contract.response_reasons:
            raise ValueError("trigger reason requires a declared response reason")
        if trigger_event is not None:
            if "event_id" not in trigger_event:
                raise ValueError("trigger_event event_id is required")
            if "type" not in trigger_event:
                raise ValueError("trigger_event type is required")
            event_id = ContextProjector._event_id(
                trigger_event["event_id"], "event_id"
            )
            if event_id != source_event_id:
                raise ValueError("trigger_event event_id must match source_event_id")
        try:
            count = len(summaries)
        except TypeError as error:
            raise TypeError("accepted command summaries must be a sized sequence") from error
        if count > _MAX_SUMMARIES:
            raise ValueError("accepted command summaries may contain at most 64 items")

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
        ContextProjector._positive_int(request.actor_seat, "actor_seat")
        ContextProjector._positive_int(actor.seat_number, "actor seat number")
        ContextProjector._nonnegative_int(request.round_number, "request round")
        ContextProjector._nonnegative_int(
            request.context_revision, "context revision"
        )
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
        if request.contract not in contracts:
            raise ValueError("request contract is not registered for actor role")
        return actor

    @classmethod
    def _public_facts(cls, state: GameState, registry: RegistrySnapshot) -> dict[str, object]:
        cls._nonnegative_int(state.round_number, "state round")
        seats = tuple(state.players)
        for seat in seats:
            cls._positive_int(seat, "player seat")
        sheriff = cls._optional_positive_int(state.sheriff, "sheriff")
        facts: dict[str, object] = {
            "alive_seats": tuple(
                sorted(seat for seat, player in state.players.items() if player.is_alive)
            ),
            "dead_seats": tuple(
                sorted(
                    seat for seat, player in state.players.items() if not player.is_alive
                )
            ),
            "phase": state.phase.value if hasattr(state.phase, "value") else state.phase,
            "round_number": state.round_number,
            "public_role_rules": tuple(
                {
                    "id": role_id,
                    "count": count,
                    "display_name": registry.specs[role_id].display_name,
                    "instructions": registry.specs[role_id].instructions,
                }
                for role_id, count in sorted(state.config.role_counts.items())
                if count and role_id in registry.specs
            ),
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
        facts["sheriff"] = sheriff
        if getattr(state.config, "enable_sheriff", False):
            facts["sheriff_enabled"] = True
        return facts

    @classmethod
    def _project_speech(cls, record: object) -> dict[str, object] | None:
        if type(record) is SpeechRecord:
            raw: Mapping[str, object] = {
                "player_seat": record.player_seat,
                "text": record.text,
                "round_number": record.round_number,
            }
        elif type(record) is dict:
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
        elif type(record) is dict:
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
        state: GameState, actor: PlayerState, declarations: Mapping[str, object]
    ) -> dict[str, object]:
        canonical = role_resource_view(state, actor.seat_number)
        resources: dict[str, object] = {}
        for key, default in declarations.items():
            if key in canonical:
                resources[key] = canonical[key]
                continue
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
    ) -> dict[str, object]:
        facts: dict[str, object] = {}
        for key, default in declarations.items():
            if key in {"private_checks", "check_results"}:
                canonical = role_private_facts_view(
                    state, actor.seat_number, "private_checks"
                )
                facts["private_checks"] = tuple(
                    projected
                    for result in (canonical or actor.check_results)[-20:]
                    if (projected := cls._project_check_result(result)) is not None
                )
            elif key in {"wolf_kill_target", "last_wolf_kill_target"}:
                # Declared private knowledge is independent of potion availability.
                facts["wolf_kill_target"] = cls._optional_positive_int(
                    state.last_wolf_kill_target, "wolf_kill_target"
                )
            else:
                canonical = role_private_data_view(state, actor.seat_number)
                facts[key] = (
                    cls._copy_json_value(canonical[key])
                    if key in canonical
                    else cls._copy_json_value(default)
                )
        return facts

    @staticmethod
    def _project_check_result(result: object) -> dict[str, object] | None:
        if type(result) not in (dict, MappingProxyType):
            return None
        target = result.get("target", result.get("target_seat"))
        camp = result.get("camp", result.get("result"))
        if (
            type(target) is not int
            or target <= 0
            or target > _MAX_INT
            or camp not in _CAMPS
        ):
            return None
        return {"target": target, "camp": camp}

    @classmethod
    def _project_trigger_event(
        cls, value: Mapping[str, object] | None, contract: object
    ) -> dict[str, object] | None:
        if value is None:
            return None
        if type(value) is not dict:
            raise TypeError("trigger_event must be an exact dict")
        event_type = cls._event_token(value["type"], "event type", 64)
        if event_type not in contract.response_event_types:
            raise ValueError("unknown event type")
        projected: dict[str, object] = {
            "event_id": cls._event_id(value["event_id"], "event_id"),
            "type": event_type,
        }
        for key in ("source_seat", "target_seat"):
            if key in value:
                projected[key] = cls._positive_int(value[key], key)
        if "cause" in value:
            cause = cls._token(value["cause"], "cause", 32)
            if cause not in contract.response_reasons:
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
    def _project_counters(
        cls, value: Mapping[str, int] | None
    ) -> dict[str, int]:
        if value is None:
            return {}
        if type(value) is not dict:
            raise TypeError("counters must be an exact dict")
        return {
            cls._token(key, "counter key", 64): cls._nonnegative_int(item, key)
            for key, item in value.items()
        }

    @classmethod
    def _project_command_result(
        cls, value: object, contract: object, name: str, aggregate: bool
    ) -> dict[str, object]:
        if type(value) is not dict:
            raise TypeError(f"{name} must be an exact dict")
        allowed = _AGGREGATE_RESULT_FIELDS if aggregate else _ACCEPTED_SUMMARY_FIELDS
        projected: dict[str, object] = {}
        if "actor_seat" in allowed and "actor_seat" in value:
            projected["actor_seat"] = cls._positive_int(
                value["actor_seat"], "actor_seat"
            )
        if "contract_id" not in value or "action_type" not in value:
            raise ValueError(f"{name} contract_id and action_type are required")
        contract_id = cls._token(value["contract_id"], "contract_id", 128)
        if contract_id != contract.contract_id:
            raise ValueError("contract_id does not match request contract")
        projected["contract_id"] = contract_id
        action_type = cls._token(value["action_type"], "action_type", 64)
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
        reason = cls._token(reason, "trigger reason", 32)
        if reason not in declared:
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
    def _token(cls, value: object, name: str, limit: int) -> str:
        token = cls._text(value, name, limit)
        if _STABLE_TOKEN.fullmatch(token) is None:
            raise ValueError(f"{name} must be a stable token")
        return token

    @classmethod
    def _event_token(cls, value: object, name: str, limit: int) -> str:
        token = cls._text(value, name, limit)
        if _EVENT_TOKEN.fullmatch(token) is None:
            raise ValueError(f"{name} must be a stable token")
        return token

    @classmethod
    def _event_id(cls, value: object, name: str) -> str:
        event_id = cls._text(value, name, 97)
        if _OPAQUE_EVENT_ID.fullmatch(event_id) is None:
            raise ValueError(f"{name} must be an opaque event id")
        return event_id

    @classmethod
    def _optional_event_id(cls, value: object, name: str) -> str | None:
        return None if value is None else cls._event_id(value, name)

    @staticmethod
    def _positive_int(value: object, name: str) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
        if value > _MAX_INT:
            raise ValueError(f"{name} exceeds the 32-bit integer limit")
        return value

    @classmethod
    def _optional_positive_int(cls, value: object, name: str) -> int | None:
        return None if value is None else cls._positive_int(value, name)

    @staticmethod
    def _nonnegative_int(value: object, name: str) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
        if value > _MAX_INT:
            raise ValueError(f"{name} exceeds the 32-bit integer limit")
        return value

    @classmethod
    def _copy_json_value(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {key: cls._copy_json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return tuple(cls._copy_json_value(item) for item in value)
        return value
