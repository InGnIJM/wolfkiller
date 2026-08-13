from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType

from app.core.state_transaction import state_transaction_lock
from app.models.game import GameState
from app.models.pipeline import EffectKind, GameEffect, RoleSpec

_INT32 = 2_147_483_647


def initialize_role_resources(state: GameState, specs: Mapping[str, RoleSpec],
                              config_version: str):
    from app.core.effect_applier import EffectApplier, EffectPermission, EffectRejected, derive_effect_id
    if type(state) is not GameState: raise TypeError("state must be GameState")
    if not isinstance(specs, Mapping) or any(type(spec) is not RoleSpec for spec in specs.values()):
        raise TypeError("specs must map role ids to exact RoleSpec values")
    if any(key != spec.role_id for key, spec in specs.items()):
        raise ValueError("spec key must match role id")
    if type(config_version) is not str or len(config_version) != 64 or any(char not in "0123456789abcdef" for char in config_version):
        raise ValueError("invalid config_version")
    with state_transaction_lock(state):
        declared = []
        assignments = []
        for seat, player in sorted(state.players.items()):
            try: spec = specs[player.role]
            except KeyError: raise ValueError(f"unknown role: {player.role}") from None
            assignments.append((seat, player.role, spec.stable_digest()))
            for name, raw in sorted(spec.initial_resources.items()):
                value = int(raw) if type(raw) is bool else raw
                if type(value) is not int or not 0 <= value <= _INT32:
                    raise EffectRejected("invalid initial resource")
                declared.append((seat, name, value))
        if not declared:
            return None
        marker = hashlib.sha256(json.dumps([config_version, assignments], separators=(",", ":")).encode()).hexdigest()
        runtime = getattr(state, "_pipeline_runtime", None)
        existing = getattr(runtime, "resource_setup_digest", None) if runtime is not None else None
        if existing is not None:
            if existing != marker:
                raise EffectRejected("role resource configuration changed")
            return runtime.commits.get(_setup_key(state.game_id))
        key = _setup_key(state.game_id); revision = 0 if runtime is None else runtime.revision
        effects = [GameEffect(derive_effect_id(key, 0), EffectKind.ACCEPT_ACTION, key, expected_revision=revision, sort_key=(0,))]
        for ordinal, (seat, name, value) in enumerate(declared, 1):
            effects.append(GameEffect(derive_effect_id(key, ordinal), EffectKind.SET_RESOURCE, key,
                target_seat=seat, payload={"target": seat, "resource": name, "value": value},
                expected_revision=revision, sort_key=(ordinal,)))
        permission = EffectPermission(declared[0][0], frozenset({EffectKind.SET_RESOURCE}),
            frozenset({EffectKind.SET_RESOURCE}), frozenset(seat for seat, _, _ in declared), frozenset())
        result = EffectApplier().apply(state, tuple(effects), permission)
        state._pipeline_runtime.resource_setup_digest = marker
        return result


def role_resource_view(state: GameState, seat: int) -> Mapping[str, int]:
    from app.core.effect_applier import EffectRejected
    if type(state) is not GameState: raise TypeError("state must be GameState")
    if type(seat) is not int or seat <= 0: raise ValueError("invalid seat")
    with state_transaction_lock(state):
        runtime = getattr(state, "_pipeline_runtime", None)
        if runtime is None:
            return MappingProxyType({})
        try: resources = runtime.clone().role_resources.get(seat, {})
        except (AttributeError, TypeError, ValueError) as error: raise EffectRejected("invalid pipeline runtime") from error
        return MappingProxyType(dict(resources))


def _setup_key(game_id: str) -> str:
    return hashlib.sha256(f"role-resource-setup\0{game_id}".encode()).hexdigest()
