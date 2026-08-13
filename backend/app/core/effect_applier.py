from __future__ import annotations
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping
from app.models.game import GameState
from app.models.pipeline import EffectKind, GameEffect
from app.core.state_transaction import state_transaction_lock
from app.core.role_runtime import initialize_role_resources, role_resource_view
INT32_MAX = 2_147_483_647
_TOKEN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
class EffectRejected(ValueError): pass
def _utf8(value: str, name: str, *, token: bool = False) -> str:
    if type(value) is not str: raise TypeError(f"{name} must be a string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error: raise ValueError(f"{name} must be valid UTF-8") from error
    if not value or len(value) > 256 or (token and _TOKEN.fullmatch(value) is None): raise ValueError(f"invalid {name}")
    return value
def _integer(value: object, name: str, *, positive: bool = False) -> int:
    if type(value) is not int: raise TypeError(f"{name} must be an integer")
    if not (1 if positive else 0) <= value <= INT32_MAX: raise ValueError(f"{name} out of range")
    return value
def derive_effect_id(action_key: str, ordinal: int, schema_version: int = 1) -> str:
    """Derive a stable effect identifier from its action and canonical ordinal."""
    _utf8(action_key, "action_key")
    _integer(ordinal, "ordinal")
    if type(schema_version) is not int: raise TypeError("schema_version must be an integer")
    if schema_version != 1: raise ValueError("unsupported schema_version")
    return hashlib.sha256(f"{schema_version}\0{action_key}\0{ordinal}".encode()).hexdigest()
def _effect_set(value: object, name: str) -> frozenset[EffectKind]:
    if type(value) is not frozenset: raise TypeError(f"{name} must be a frozenset")
    if any(type(item) is not EffectKind for item in value): raise TypeError(f"{name} elements must be EffectKind")
    return value
@dataclass(frozen=True)
class EffectPermission:
    actor_seat: int
    role_effects: frozenset[EffectKind]
    contract_effects: frozenset[EffectKind]
    allowed_targets: frozenset[int]
    allowed_visibility: frozenset[str]
    schema_version: int = 1
    def __post_init__(self) -> None:
        _integer(self.actor_seat, "actor_seat", positive=True)
        if type(self.schema_version) is not int: raise TypeError("schema_version must be an integer")
        if self.schema_version != 1: raise ValueError("unsupported schema_version")
        for name in ("role_effects", "contract_effects"): object.__setattr__(self, name, _effect_set(getattr(self, name), name))
        if type(self.allowed_targets) is not frozenset: raise TypeError("allowed_targets must be a frozenset")
        for seat in self.allowed_targets: _integer(seat, "allowed target", positive=True)
        if type(self.allowed_visibility) is not frozenset: raise TypeError("allowed_visibility must be a frozenset")
        for visibility in self.allowed_visibility: _utf8(visibility, "allowed visibility", token=True)
    @property
    def allowed_effects(self) -> frozenset[EffectKind]:
        return self.role_effects & self.contract_effects | {EffectKind.ACCEPT_ACTION}
def _json(value: object, name: str = "value", depth: int = 0,
          active: set[int] | None = None, nodes: list[int] | None = None) -> object:
    if depth > 64: raise EffectRejected(f"{name} exceeds maximum depth")
    nodes = [0] if nodes is None else nodes; nodes[0] += 1
    if nodes[0] > 10_000: raise EffectRejected(f"{name} is too large")
    if value is None or type(value) is bool: return value
    if type(value) is int:
        try: return _integer(value, name)
        except (TypeError, ValueError) as error: raise EffectRejected(str(error)) from error
    if type(value) is float:
        if math.isfinite(value): return value
        raise EffectRejected(f"invalid {name} number")
    if type(value) is str:
        try: return _utf8(value, name)
        except (TypeError, ValueError) as error: raise EffectRejected(str(error)) from error
    if not isinstance(value, (Mapping, tuple, list)): raise EffectRejected(f"invalid {name} value")
    active = set() if active is None else active; identity = id(value)
    if identity in active: raise EffectRejected(f"{name} contains cycle")
    active.add(identity); items = value.items() if isinstance(value, Mapping) else enumerate(value)
    if isinstance(value, Mapping):
        for key in value:
            if type(key) is not str: raise EffectRejected(f"invalid {name} key")
            _utf8(key, f"{name} key")
    cloned = [(key, _json(item, f"{name}.{key}", depth + 1, active, nodes)) for key, item in items]
    active.remove(identity); return MappingProxyType(dict(cloned)) if isinstance(value, Mapping) else tuple(item for _, item in cloned)
@dataclass(frozen=True)
class CommitResult:
    action_key: str
    effect_ids: tuple[str, ...]
    revision: int
    events: tuple[Mapping[str, object], ...]
    state_digest: str
    schema_version: int = 1
    def __post_init__(self) -> None:
        _utf8(self.action_key, "action_key")
        if type(self.effect_ids) is not tuple: raise TypeError("effect_ids must be a tuple")
        for effect_id in self.effect_ids: _utf8(effect_id, "effect_id", token=True)
        _integer(self.revision, "revision")
        if type(self.events) is not tuple: raise TypeError("events must be a tuple")
        if any(not isinstance(event, Mapping) for event in self.events): raise TypeError("events must contain mappings")
        _utf8(self.state_digest, "state_digest")
        if type(self.schema_version) is not int: raise TypeError("schema_version must be an integer")
        if self.schema_version != 1: raise ValueError("unsupported schema_version")
        object.__setattr__(self, "events", tuple(_json(event, "event") for event in self.events))
@dataclass
class _Runtime:
    revision: int = 0
    role_resources: dict[int, dict[str, int]] = field(default_factory=dict)
    private_data: dict[int, dict[str, object]] = field(default_factory=dict)
    statuses: dict[int, set[str]] = field(default_factory=dict)
    relations: dict[int, set[tuple[str, int]]] = field(default_factory=dict)
    private_facts: dict[int, list[dict[str, object]]] = field(default_factory=dict)
    pending_damage: tuple[dict[str, object], ...] = ()
    pending_protection: tuple[dict[str, object], ...] = ()
    events: tuple[Mapping[str, object], ...] = ()
    commits: dict[str, CommitResult] = field(default_factory=dict)
    resource_setup_digest: str | None = None
    def clone(self) -> "_Runtime":
        try:
            revision = _integer(self.revision, "runtime revision")
            resources = _resource_map(self.role_resources)
            statuses = _set_map(self.statuses, "status", relation=False)
            relations = _set_map(self.relations, "relation", relation=True)
            data = _private_data(self.private_data)
            facts = _private_facts(self.private_facts)
            damage = _json_sequence(self.pending_damage, "pending_damage")
            protection = _json_sequence(self.pending_protection, "pending_protection")
            events = _json_sequence(self.events, "events")
            commits = _commit_map(self.commits)
        except (AttributeError, TypeError, ValueError) as error:
            raise EffectRejected("invalid pipeline runtime") from error
        marker = self.resource_setup_digest
        if marker is not None:
            try: _utf8(marker, "resource setup digest", token=True)
            except (TypeError, ValueError) as error: raise EffectRejected("invalid pipeline runtime") from error
        return _Runtime(revision, resources, data, statuses, relations, facts,
                        damage, protection, events, commits, marker)
def _seat(seat: object) -> int:
    try: return _integer(seat, "runtime seat", positive=True)
    except (TypeError, ValueError) as error: raise EffectRejected(str(error)) from error
def _resource_map(value: object) -> dict[int, dict[str, int]]:
    if not isinstance(value, Mapping): raise EffectRejected("invalid role_resources")
    result = {}
    for seat, resources in value.items():
        if not isinstance(resources, Mapping): raise EffectRejected("invalid role_resources")
        row = {}
        for name, amount in resources.items():
            try: row[_utf8(name, "resource", token=True)] = _integer(amount, "resource value")
            except (TypeError, ValueError) as error: raise EffectRejected(str(error)) from error
        result[_seat(seat)] = row
    return result
def _set_map(value: object, name: str, *, relation: bool) -> dict[int, set]:
    if not isinstance(value, Mapping): raise EffectRejected(f"invalid {name}s")
    result = {_seat(seat): {_set_item(item, name, relation) for item in values}
              for seat, values in value.items() if type(values) is set}
    if len(result) != len(value): raise EffectRejected(f"invalid {name}s")
    return result
def _set_item(item: object, name: str, relation: bool) -> object:
    try:
        if not relation: return _utf8(item, name, token=True)
        if type(item) is not tuple or len(item) != 2: raise TypeError
        return (_utf8(item[0], name, token=True), _integer(item[1], "relation seat", positive=True))
    except (TypeError, ValueError) as error: raise EffectRejected(f"invalid {name}") from error
def _private_data(value: object) -> dict[int, dict]:
    if not isinstance(value, Mapping): raise EffectRejected("invalid private_data")
    if any(not isinstance(item, Mapping) for item in value.values()): raise EffectRejected("invalid private_data")
    return {_seat(seat): dict(_json(item, "private_data")) for seat, item in value.items()}
def _private_facts(value: object) -> dict[int, list]:
    return dict(_private_fact_row(seat, records) for seat, records in value.items())
def _private_fact_row(seat: object, records: object) -> tuple[int, list]:
    _fact_sequence(records); return _seat(seat), [_private_fact(record) for record in records]
def _fact_sequence(value: object) -> bool:
    if type(value) not in (list, tuple): raise EffectRejected("invalid private_facts")
    return True
def _private_fact(record: object) -> dict:
    if not isinstance(record, Mapping) or set(record) != {"namespace", "fact"} or not isinstance(record.get("fact"), Mapping): raise EffectRejected("invalid private fact")
    return {"namespace": _token_field(record, "namespace"), "fact": _json(record["fact"], "private_fact")}
def _json_sequence(value: object, name: str) -> tuple:
    if type(value) not in (list, tuple): raise EffectRejected(f"invalid {name}")
    return tuple(_json(item, name) for item in value)
def _commit_map(value: object) -> dict[str, CommitResult]:
    if not isinstance(value, Mapping): raise EffectRejected("invalid commits")
    result = {}
    for key, commit in value.items():
        if type(key) is not str or type(commit) is not CommitResult or key != commit.action_key: raise EffectRejected("invalid commit ledger")
        result[key] = commit
    return result
def _runtime(state: GameState) -> _Runtime:
    value = getattr(state, "_pipeline_runtime", None)
    if value is None: return _Runtime()
    if type(value) is not _Runtime: raise EffectRejected("invalid pipeline runtime")
    return value
def _exact(mapping: Mapping[str, object], fields: frozenset[str], name: str) -> None:
    if frozenset(mapping) != fields: raise EffectRejected(f"invalid {name} fields")
def _payload_target(effect: GameEffect, payload: Mapping[str, object]) -> int:
    target = _integer(payload["target"], "payload target", positive=True)
    if effect.target_seat != target: raise EffectRejected("payload target does not match target_seat")
    return target
def _token_field(payload: Mapping[str, object], name: str) -> str:
    try:
        return _utf8(payload[name], name, token=True)
    except (TypeError, ValueError) as error:
        raise EffectRejected(str(error)) from error
def _int_field(payload: Mapping[str, object], name: str, *, positive: bool = False) -> int:
    try:
        return _integer(payload[name], name, positive=positive)
    except (TypeError, ValueError) as error:
        raise EffectRejected(str(error)) from error
def _validate_payload(effect: GameEffect, seats: set[int]) -> dict[str, object]:
    payload = dict(effect.payload)
    _json(effect.payload, "payload")
    kind = effect.kind
    schemas = {
        EffectKind.ACCEPT_ACTION: frozenset(),
        EffectKind.CONSUME_RESOURCE: frozenset({"target", "resource", "amount"}),
        EffectKind.SET_RESOURCE: frozenset({"target", "resource", "value"}),
        EffectKind.SET_PRIVATE_DATA: frozenset({"target", "key", "value"}),
        **{kind: frozenset({"target", "status"}) for kind in (EffectKind.ADD_STATUS, EffectKind.REMOVE_STATUS)},
        **{kind: frozenset({"target", "relation", "other_seat"}) for kind in (EffectKind.ADD_RELATION, EffectKind.REMOVE_RELATION)},
        EffectKind.RECORD_PRIVATE_FACT: frozenset({"target", "namespace", "fact"}),
        **{kind: frozenset({"target", "amount"}) for kind in (EffectKind.SUBMIT_DAMAGE, EffectKind.SUBMIT_PROTECTION)},
        EffectKind.MARK_DEATH: frozenset({"target", "cause"}),
        EffectKind.EMIT_EVENT: frozenset({"event_type", "payload"}),
    }
    _exact(payload, schemas[kind], "payload")
    if kind is EffectKind.ACCEPT_ACTION:
        if effect.target_seat is not None: raise EffectRejected("accept action cannot have target")
        return payload
    if kind is EffectKind.EMIT_EVENT:
        _token_field(payload, "event_type")
        if not isinstance(payload["payload"], Mapping): raise EffectRejected("event payload must be a mapping")
        if effect.target_seat is not None and effect.target_seat not in seats: raise EffectRejected("target seat does not exist")
        return payload
    target = _payload_target(effect, payload)
    if target not in seats: raise EffectRejected("target seat does not exist")
    if kind in {EffectKind.CONSUME_RESOURCE, EffectKind.SET_RESOURCE}:
        _token_field(payload, "resource")
        _int_field(payload, "amount" if kind is EffectKind.CONSUME_RESOURCE else "value", positive=kind is EffectKind.CONSUME_RESOURCE)
    elif kind is EffectKind.SET_PRIVATE_DATA:
        _token_field(payload, "key")
    elif kind in {EffectKind.ADD_STATUS, EffectKind.REMOVE_STATUS}:
        _token_field(payload, "status")
    elif kind in {EffectKind.ADD_RELATION, EffectKind.REMOVE_RELATION}:
        _token_field(payload, "relation")
        other = _int_field(payload, "other_seat", positive=True)
        if other not in seats: raise EffectRejected("relation seat does not exist")
    elif kind is EffectKind.RECORD_PRIVATE_FACT:
        _token_field(payload, "namespace")
        if not isinstance(payload["fact"], Mapping): raise EffectRejected("fact must be a mapping")
    elif kind in {EffectKind.SUBMIT_DAMAGE, EffectKind.SUBMIT_PROTECTION}:
        _int_field(payload, "amount", positive=True)
    else:
        try:
            _utf8(payload["cause"], "cause")
        except (TypeError, ValueError) as error:
            raise EffectRejected(str(error)) from error
    return payload
def _check_preconditions(effect: GameEffect, runtime: _Runtime, alive: dict[int, bool]) -> None:
    allowed = frozenset({"target_alive", "resource_equals", "private_equals", "status_present"})
    if not set(effect.preconditions) <= allowed: raise EffectRejected("unknown precondition")
    target = effect.target_seat
    if "target_alive" in effect.preconditions:
        expected = effect.preconditions["target_alive"]
        if type(expected) is not bool or target is None or alive[target] is not expected: raise EffectRejected("target_alive precondition failed")
    if "resource_equals" in effect.preconditions:
        condition = effect.preconditions["resource_equals"]
        if not isinstance(condition, Mapping): raise EffectRejected("resource precondition invalid")
        _exact(condition, frozenset({"resource", "value"}), "resource precondition")
        resource = _token_field(condition, "resource")
        value = _int_field(condition, "value")
        if target is None or runtime.role_resources.get(target, {}).get(resource, 0) != value: raise EffectRejected("resource precondition failed")
    if "private_equals" in effect.preconditions:
        condition = effect.preconditions["private_equals"]
        if not isinstance(condition, Mapping): raise EffectRejected("private precondition invalid")
        _exact(condition, frozenset({"key", "value"}), "private precondition")
        key = _token_field(condition, "key")
        if target is None or runtime.private_data.get(target, {}).get(key) != condition["value"]: raise EffectRejected("private precondition failed")
    if "status_present" in effect.preconditions:
        condition = effect.preconditions["status_present"]
        if type(condition) is str:
            status, present = _utf8(condition, "status", token=True), True
        elif isinstance(condition, Mapping):
            _exact(condition, frozenset({"status", "present"}), "status precondition")
            status = _token_field(condition, "status")
            present = condition["present"]
            if type(present) is not bool: raise EffectRejected("status precondition invalid")
        else:
            raise EffectRejected("status precondition invalid")
        actual = target is not None and status in runtime.statuses.get(target, set())
        if actual is not present: raise EffectRejected("status precondition failed")
def _apply_one(effect: GameEffect, payload: dict[str, object], runtime: _Runtime,
               alive: dict[int, bool], events: list[Mapping[str, object]]) -> None:
    kind = effect.kind
    if kind is EffectKind.ACCEPT_ACTION: return
    if kind is EffectKind.EMIT_EVENT:
        events.append({"event_type": payload["event_type"], "payload": payload["payload"], "visibility": effect.visibility}); return
    target = effect.target_seat; assert target is not None
    if kind is EffectKind.SET_RESOURCE:
        runtime.role_resources.setdefault(target, {})[str(payload["resource"])] = int(payload["value"])
    elif kind is EffectKind.CONSUME_RESOURCE:
        resources = runtime.role_resources.setdefault(target, {})
        resource = str(payload["resource"]); amount = int(payload["amount"])
        if resources.get(resource, 0) < amount: raise EffectRejected("resource underflow")
        resources[resource] -= amount
    elif kind is EffectKind.SET_PRIVATE_DATA:
        runtime.private_data.setdefault(target, {})[str(payload["key"])] = payload["value"]
    elif kind in {EffectKind.ADD_STATUS, EffectKind.REMOVE_STATUS}:
        statuses = runtime.statuses.setdefault(target, set()); status = str(payload["status"])
        if kind is EffectKind.ADD_STATUS:
            if status in statuses: raise EffectRejected("status already present")
            statuses.add(status)
        else:
            if status not in statuses: raise EffectRejected("status is absent")
            statuses.remove(status)
    elif kind in {EffectKind.ADD_RELATION, EffectKind.REMOVE_RELATION}:
        relations = runtime.relations.setdefault(target, set())
        relation = (str(payload["relation"]), int(payload["other_seat"]))
        if kind is EffectKind.ADD_RELATION:
            if relation in relations: raise EffectRejected("relation already present")
            relations.add(relation)
        else:
            if relation not in relations: raise EffectRejected("relation is absent")
            relations.remove(relation)
    elif kind is EffectKind.RECORD_PRIVATE_FACT:
        runtime.private_facts.setdefault(target, []).append({"namespace": payload["namespace"], "fact": payload["fact"]})
    elif kind is EffectKind.SUBMIT_DAMAGE:
        runtime.pending_damage += ({"target": target, "amount": payload["amount"]},)
    elif kind is EffectKind.SUBMIT_PROTECTION:
        runtime.pending_protection += ({"target": target, "amount": payload["amount"]},)
    else:
        if not alive[target]: raise EffectRejected("player already dead")
        alive[target] = False
        events.append({"event_type": "PLAYER_DIED", "payload": {"seat": target, "cause": payload["cause"]}, "visibility": effect.visibility})
def _jsonable(value: object) -> object:
    if isinstance(value, Mapping): return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list, set, frozenset)):
        items = [_jsonable(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False)) if isinstance(value, (set, frozenset)) else items
    return value
def _digest(state: GameState, runtime: _Runtime, alive: dict[int, bool]) -> str:
    document = {
        "game_id": state.game_id,
        "revision": runtime.revision,
        "alive": alive,
        "role_resources": runtime.role_resources,
        "private_data": runtime.private_data,
        "statuses": runtime.statuses,
        "relations": runtime.relations,
        "private_facts": runtime.private_facts,
        "pending_damage": runtime.pending_damage,
        "pending_protection": runtime.pending_protection,
        "events": runtime.events,
        "resource_setup_digest": runtime.resource_setup_digest,
    }
    return hashlib.sha256(json.dumps(_jsonable(document), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
class EffectApplier:
    def apply(self, state: GameState, effects: tuple[GameEffect, ...],
              permission: EffectPermission) -> CommitResult:
        if type(state) is not GameState: raise TypeError("state must be GameState")
        observed_revision = _runtime(state).revision
        with state_transaction_lock(state):
            return self._apply_locked(state, effects, permission, observed_revision)

    def _apply_locked(self, state: GameState, effects: tuple[GameEffect, ...],
                      permission: EffectPermission, observed_revision: int) -> CommitResult:
        if type(effects) is not tuple: raise TypeError("effects must be a tuple")
        if type(permission) is not EffectPermission: raise TypeError("permission must be EffectPermission")
        if not effects or any(type(effect) is not GameEffect for effect in effects): raise EffectRejected("effects must contain GameEffect values")
        action_keys = {effect.source_action_key for effect in effects}
        if len(action_keys) != 1: raise EffectRejected("all effects must have one action key")
        action_key = next(iter(action_keys))
        current = _runtime(state)
        simulated = current.clone()
        if action_key in current.commits: return current.commits[action_key]
        ordered = tuple(sorted(effects, key=lambda effect: (effect.sort_key, effect.effect_id)))
        ids = [effect.effect_id for effect in ordered]
        if len(ids) != len(set(ids)): raise EffectRejected("duplicate effect id")
        if sum(effect.kind is EffectKind.ACCEPT_ACTION for effect in ordered) != 1: raise EffectRejected("batch must contain exactly one accept action")
        for ordinal, effect in enumerate(ordered):
            if effect.effect_id != derive_effect_id(action_key, ordinal): raise EffectRejected("effect id does not match canonical ordinal")
            if effect.kind not in permission.allowed_effects: raise EffectRejected("effect permission denied")
            if effect.expected_revision != observed_revision: raise EffectRejected("revision mismatch")
            if effect.target_seat is not None and effect.target_seat not in permission.allowed_targets: raise EffectRejected("target permission denied")
            if len(effect.visibility) != len(set(effect.visibility)) or not set(effect.visibility) <= permission.allowed_visibility: raise EffectRejected("visibility permission denied")
        alive = {seat: player.is_alive for seat, player in state.players.items()}
        generated_events: list[Mapping[str, object]] = []
        for effect in ordered:
            payload = _validate_payload(effect, set(state.players))
            _check_preconditions(effect, simulated, alive)
            _apply_one(effect, payload, simulated, alive, generated_events)
        simulated.revision += 1
        simulated.events += tuple(_json(event, "event") for event in generated_events)
        digest = _digest(state, simulated, alive)
        result = CommitResult(action_key, tuple(ids), simulated.revision, tuple(generated_events), digest)
        simulated.commits[action_key] = result
        for seat, is_alive in alive.items(): state.players[seat].is_alive = is_alive
        setattr(state, "_pipeline_runtime", simulated)
        return result
