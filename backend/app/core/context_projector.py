"""Project mutable game state into a minimal, deeply frozen action context."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
            source_event_id=source_event_id,
            trigger_event=self._project_allowlisted_mapping(
                trigger_event, _TRIGGER_EVENT_FIELDS, "trigger_event"
            ),
            trigger_reason=self._validate_trigger_reason(trigger_reason),
            accepted_command_summaries=tuple(
                self._project_required_mapping(
                    summary, _ACCEPTED_SUMMARY_FIELDS, "accepted command summary"
                )
                for summary in accepted_command_summaries
            ),
            aggregate_result=self._project_allowlisted_mapping(
                aggregate_result, _AGGREGATE_RESULT_FIELDS, "aggregate_result"
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
                cls._allowlisted_record(record, _SPEECH_FIELDS)
                for record in state.speeches
            ),
            "votes": tuple(
                cls._allowlisted_record(record, _VOTE_FIELDS) for record in state.votes
            ),
        }

    @staticmethod
    def _allowlisted_record(record: object, fields: tuple[str, ...]) -> dict[str, object]:
        raw = record.to_dict() if hasattr(record, "to_dict") else record
        if not isinstance(raw, Mapping):
            return {}
        return {field: raw[field] for field in fields if field in raw}

    @staticmethod
    def _actor_resources(
        actor: PlayerState, declarations: Mapping[str, object]
    ) -> dict[str, object]:
        resources: dict[str, object] = {}
        for key, default in declarations.items():
            if hasattr(actor, key):
                value = getattr(actor, key)
            elif hasattr(actor, f"has_{key}"):
                value = getattr(actor, f"has_{key}")
            elif key.startswith("has_") and hasattr(actor, key[4:]):
                value = getattr(actor, key[4:])
            else:
                value = default
            resources[key] = value
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
                    cls._project_check_result(result) for result in actor.check_results
                )
            elif key in {"wolf_kill_target", "last_wolf_kill_target"}:
                has_antidote = bool(
                    resources.get("antidote", resources.get("has_antidote", False))
                )
                if has_antidote:
                    facts["wolf_kill_target"] = state.last_wolf_kill_target
            elif hasattr(actor, key):
                facts[key] = cls._copy_json_value(getattr(actor, key))
            else:
                facts[key] = cls._copy_json_value(default)
        return facts

    @staticmethod
    def _project_check_result(result: object) -> dict[str, object]:
        if not isinstance(result, Mapping):
            return {}
        projected: dict[str, object] = {}
        if "target" in result or "target_seat" in result:
            projected["target"] = result.get("target", result.get("target_seat"))
        if "camp" in result or "result" in result:
            projected["camp"] = result.get("camp", result.get("result"))
        return projected

    @classmethod
    def _project_allowlisted_mapping(
        cls,
        value: Mapping[str, object] | None,
        allowed_fields: frozenset[str],
        name: str,
    ) -> dict[str, object] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise TypeError(f"{name} must be a mapping")
        return {
            key: item
            for key, item in value.items()
            if key in allowed_fields and cls._is_scalar(item)
        }

    @classmethod
    def _project_required_mapping(
        cls, value: object, allowed_fields: frozenset[str], name: str
    ) -> dict[str, object]:
        if not isinstance(value, Mapping):
            raise TypeError(f"{name} must be a mapping")
        projected = cls._project_allowlisted_mapping(value, allowed_fields, name)
        assert projected is not None
        return projected

    @staticmethod
    def _validate_trigger_reason(reason: str | None) -> str | None:
        if reason is None:
            return None
        if reason not in _PUBLIC_TRIGGER_REASONS:
            raise ValueError("unknown public trigger reason")
        return reason

    @staticmethod
    def _is_scalar(value: object) -> bool:
        return value is None or type(value) in (bool, int, float, str)

    @classmethod
    def _copy_json_value(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return {key: cls._copy_json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return tuple(cls._copy_json_value(item) for item in value)
        return value
