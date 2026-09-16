from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

_INT32 = 2_147_483_647


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
) -> tuple[tuple[dict[str, object], ...], dict[int, bool]]:
    if type(damage) is not tuple or type(protection) is not tuple:
        raise TypeError("pending effects must be tuples")
    if type(seats) is not set or any(type(seat) is not int or seat <= 0 for seat in seats):
        raise ValueError("invalid seats")
    if not isinstance(alive, Mapping) or set(alive) != seats or any(type(value) is not bool for value in alive.values()):
        raise ValueError("invalid alive map")
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
    resulting_alive = dict(alive); deaths = []
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
        resulting_alive[target] = False
        deaths.append({"seat": target, "cause": cause, "round_number": round_number})
    return tuple(deaths), resulting_alive


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
