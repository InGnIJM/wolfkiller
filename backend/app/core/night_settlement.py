from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

_INT32 = 2_147_483_647

# Role-agnostic marker that turns a lethal hit into a delayed death. A role
# declares it in ``initial_resources`` (the old drunkard does) and the
# settlement honours the mark; the engine finalises the pending death on the
# next day. ``DELAYED_DEATH_STATUS`` is the runtime status the applier writes so
# the pending death stays observable until then.
DELAYABLE_RESOURCE = "delayable"
DELAYED_DEATH_STATUS = "delayed_death"
POISONED_STATUS = "poisoned"
WOUNDED_STATUS = "wounded"
# Causes whose lethal hit leaves a visible poisoned / wounded mark. Anything
# else (a wolf kill) kills the delayable seat on the spot, as the rules require.
_POISON_CAUSES = frozenset({"poison"})
_WOUND_CAUSES = frozenset({"hunter_shot"})
_DELAY_CAUSES = _POISON_CAUSES | _WOUND_CAUSES
_DELAYED_DEATH_CAUSES = {
    POISONED_STATUS: "poison",
    WOUNDED_STATUS: "hunter_shot",
}


def delayed_death_statuses(cause: object) -> tuple[str, ...]:
    """Statuses a lethal ``cause`` leaves on a delayable seat, if any."""
    if cause in _POISON_CAUSES:
        return (POISONED_STATUS, DELAYED_DEATH_STATUS)
    if cause in _WOUND_CAUSES:
        return (WOUNDED_STATUS, DELAYED_DEATH_STATUS)
    return ()


def resolve_delayed_deaths(
    statuses: Mapping[int, object], round_number: int,
) -> tuple[tuple[dict[str, object], ...], dict[int, tuple[str, ...]]]:
    """Finalise the deaths a delayable seat is still owed.

    Returns the death records and, per affected seat, the statuses to clear
    (the delay marks plus the poisoned / wounded mark that explains the cause).
    The engine calls this once the next day's speeches are over.
    """
    if type(round_number) is not int or not 0 <= round_number <= _INT32:
        raise ValueError("round_number out of range")
    deaths: list[dict[str, object]] = []
    cleared: dict[int, tuple[str, ...]] = {}
    for seat in sorted(statuses):
        held = frozenset(statuses[seat])
        if DELAYED_DEATH_STATUS not in held:
            continue
        cause = next(
            (
                mapped for status, mapped in _DELAYED_DEATH_CAUSES.items()
                if status in held
            ),
            "delayed_death",
        )
        deaths.append({"seat": seat, "cause": cause, "round_number": round_number})
        cleared[seat] = tuple(sorted(
            status for status in held
            if status in _DELAYED_DEATH_CAUSES or status == DELAYED_DEATH_STATUS
        ))
    return tuple(deaths), cleared


def settlement_key(game_id: str, round_number: int, batch: int = 0) -> str:
    if type(game_id) is not str or not game_id:
        raise ValueError("invalid game id")
    try: game_id.encode("utf-8", errors="strict")
    except UnicodeEncodeError: raise ValueError("invalid game id") from None
    if type(round_number) is not int: raise TypeError("round_number must be an integer")
    if not 0 <= round_number <= _INT32: raise ValueError("round_number out of range")
    if type(batch) is not int: raise TypeError("batch must be an integer")
    if not 0 <= batch <= _INT32: raise ValueError("batch out of range")
    label = f"night-commit\0{game_id}\0{round_number}"
    if batch: label += f"\0{batch}"
    return hashlib.sha256(label.encode()).hexdigest()


def settle(
    damage: tuple[object, ...], protection: tuple[object, ...],
    seats: set[int], alive: Mapping[int, bool], round_number: int,
    role_resources: Mapping[int, Mapping[str, int]] | None = None,
) -> tuple[tuple[dict[str, object], ...], dict[int, bool], dict[int, tuple[str, ...]]]:
    """Fold pending damage and protection into this round's deaths.

    A seat that declares the ``delayable`` resource and takes a lethal hit from
    a poisoning or wounding cause survives the settlement: it is reported in the
    third return value together with the statuses the caller must apply, and
    dies on the next day instead.
    """
    if type(damage) is not tuple or type(protection) is not tuple:
        raise TypeError("pending effects must be tuples")
    if type(seats) is not set or any(type(seat) is not int or seat <= 0 for seat in seats):
        raise ValueError("invalid seats")
    if not isinstance(alive, Mapping) or set(alive) != seats or any(type(value) is not bool for value in alive.values()):
        raise ValueError("invalid alive map")
    if role_resources is None:
        role_resources = {}
    elif not isinstance(role_resources, Mapping) or any(
        type(seat) is not int or not isinstance(amounts, Mapping)
        for seat, amounts in role_resources.items()
    ):
        raise ValueError("invalid role resource map")
    settlement_key("validation", round_number)
    damage_totals, protection_totals = {}, {}
    wolf_totals, other_totals = {}, {}
    protection_sources: dict[int, set[str]] = {}
    causes: dict[int, str] = {}
    first_wolf_cause: dict[int, str] = {}
    first_other_cause: dict[int, str] = {}
    damage_causes: dict[int, set[str]] = {}
    for record in damage:
        target, amount = _record(record, seats, frozenset({"target", "amount", "cause"}))
        cause = record["cause"]
        if type(cause) is not str or not cause or len(cause) > 128 or any(
            not (char.isalnum() or char in "_.:-") for char in cause
        ): raise ValueError("invalid pending damage cause")
        _add(damage_totals, target, amount)
        causes.setdefault(target, cause)
        damage_causes.setdefault(target, set()).add(cause)
        if cause == "wolf_kill":
            _add(wolf_totals, target, amount)
            first_wolf_cause.setdefault(target, cause)
        else:
            _add(other_totals, target, amount)
            first_other_cause.setdefault(target, cause)
    for record in protection:
        target, amount = _record(record, seats, frozenset({"target", "amount"}))
        _add(protection_totals, target, amount)
        source = _protection_source(record)
        if source is not None:
            protection_sources.setdefault(target, set()).add(source)
    resulting_alive = dict(alive); deaths = []; delayed: dict[int, tuple[str, ...]] = {}
    for target in sorted(damage_totals):
        double_save = (
            "wolf_kill" in damage_causes[target]
            and {"guard", "witch_antidote"}.issubset(protection_sources.get(target, set()))
        )
        lethal_wolf = double_save or wolf_totals.get(target, 0) > protection_totals.get(target, 0)
        lethal_other = other_totals.get(target, 0) > 0
        if not resulting_alive[target] or not (lethal_wolf or lethal_other):
            continue
        if lethal_wolf and lethal_other:
            cause = causes[target]
        elif lethal_wolf:
            cause = first_wolf_cause[target]
        else:
            cause = first_other_cause[target]
        if role_resources.get(target, {}).get(DELAYABLE_RESOURCE, 0) > 0:
            # The seat survives this settlement and dies on the next day; the
            # caller applies the returned marks so the delay is observable.
            held = delayed_death_statuses(cause)
            if held:
                delayed[target] = held
                continue
        resulting_alive[target] = False
        deaths.append({"seat": target, "cause": cause, "round_number": round_number})
    return tuple(deaths), resulting_alive, delayed


def _record(record: object, seats: set[int], fields: frozenset[str]) -> tuple[int, int]:
    if not isinstance(record, Mapping) or not fields.issubset(record) or frozenset(record) - fields - {"source"}:
        raise ValueError("invalid pending effect")
    target, amount = record["target"], record["amount"]
    if type(target) is not int or target not in seats:
        raise ValueError("invalid pending target")
    if type(amount) is not int or not 1 <= amount <= _INT32:
        raise ValueError("invalid pending amount")
    return target, amount


def _protection_source(record: object) -> str | None:
    if not isinstance(record, Mapping):
        raise ValueError("invalid pending effect")
    source = record.get("source")
    if source is None:
        return None
    if source not in {"guard", "witch_antidote"}:
        raise ValueError("invalid protection source")
    return source


def _add(totals: dict[int, int], target: int, amount: int) -> None:
    total = totals.get(target, 0) + amount
    if total > _INT32: raise ValueError("pending amount overflow")
    totals[target] = total


def state_digest(state: object, runtime: object, alive: dict[int, bool]) -> str:
    document = {
        "game_id": state.game_id, "revision": runtime.revision, "alive": alive,
        "role_resources": runtime.role_resources, "private_data": runtime.private_data,
        "statuses": runtime.statuses, "relations": runtime.relations,
        "private_facts": runtime.private_facts, "pending_damage": runtime.pending_damage,
        "pending_protection": runtime.pending_protection, "events": runtime.events,
        "resource_setup_digest": runtime.resource_setup_digest,
        "action_counts": runtime.action_counts,
        "vote_receipts": runtime.vote_receipts,
    }
    return hashlib.sha256(json.dumps(_jsonable(document), ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (tuple, list, set, frozenset)):
        items = [_jsonable(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False)) if isinstance(value, (set, frozenset)) else items
    return value
