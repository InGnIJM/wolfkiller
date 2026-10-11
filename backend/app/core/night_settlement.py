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
#: Shields that cancel the wolf kill — the long-standing "guard" semantics. An
#: unnamed protection keeps that meaning, so a shield that declares no source
#: still stops the knife.
_WOLF_ONLY_SOURCES = frozenset({"guard", "witch_antidote", "dance_veil"})
#: Shields that count towards the "guard and antidote on the same target" breach.
#: Deliberately narrower than the wolf-kill tier: a dream weave is not a guard,
#: so dreaming a target the witch also saves does not breach it — and neither is
#: the Masquerade's dance veil.
_DOUBLE_SAVE_SOURCES = frozenset({"guard", "witch_antidote"})
#: Shields that cancel *any* lethal night damage, the poison included. A dream
#: weave is the first of these. Such a shield also stops the knife, so the wolf
#: total is measured against both tiers together.
_ANY_DAMAGE_SOURCES = frozenset({"dream"})
#: Public alias: the effect write boundary validates against this list, so
#: a shield tier added here can never be rejected as an unknown source.
_PROTECTION_SOURCES = _WOLF_ONLY_SOURCES | _ANY_DAMAGE_SOURCES
PROTECTION_SOURCES = _PROTECTION_SOURCES

#: A seat that declares this marker shrugs off *every* lethal night effect —
#: the wolf kill, the poison and anything else that settles here. Unlike
#: ``delayable`` it leaves no mark and never dies later: the hit simply does not
#: land. The Evil Knight is the only built-in holder.
NIGHT_IMMUNE_RESOURCE = "night_immune"
#: A seat that declares this marker survives its first lethal hit, and the seat
#: the marker names dies in its place (the Awakened Wolf Beauty's 挽歌幻象).
#: The replacement is a direct death, so the stand-in cannot be guarded out of
#: it, and the hit is judged *before* the shield tiers: the point of the skill is
#: that it lands whatever was aimed at the victim.
STAND_IN_RESOURCE = "stand_in"
#: Set alongside ``stand_in = 0`` when the replacement happens. A once-per-game
#: effect cannot be expressed by the target marker alone (it starts at zero
#: before the first charm), so the contract that offers the skill reads this.
STAND_IN_USED_RESOURCE = "stand_in_used"
#: The cause a stand-in death is reported with.
STAND_IN_CAUSE = "stand_in"
#: Runtime status a seat carries while it may not act this night. Two roles
#: apply it: the Nightmare (one feared seat, and the whole pack when it fears a
#: wolf) and the Crimson Envoy (every living good seat after its self-destruct).
#: It is read in two places, both role-agnostic: the scheduler skips the seat's
#: contract requests, and the engine withholds the wolf pack's attack.
SILENCED_STATUS = "silenced"
#: Statuses whose lifetime is exactly one night. They are swept inside the
#: same atomic batch as ``NIGHT_COMMIT``, so a seat can never carry yesterday's
#: silence into a later night. Daytime settlements (an exile reaction, a day
#: interruption) must never sweep: the Crimson Envoy's silence is applied
#: during the day and has to survive into the night it applies to.
NIGHT_SCOPED_STATUSES = frozenset({SILENCED_STATUS})


def is_silenced(statuses: Mapping[int, object], seat: int) -> bool:
    """True when ``seat`` carries the one-night silence mark."""
    return SILENCED_STATUS in frozenset(statuses.get(seat, ()))

#: A seat that declares this marker is untouched by the witch's vial. Unlike
#: ``night_immune`` it filters by cause, so the knife and every other lethal
#: effect still land. The Demon Hunter is the first built-in holder.
POISON_IMMUNE_RESOURCE = "poison_immune"
#: Lethal causes the ``night_immune`` marker does **not** shrug off.
#:
#: The official card limits the immunity to 「**无法在夜间出局**」 — the night —
#: and both the official 2018 guide and the official handbook spell out the one
#: exception: 「除了**猎枪**，恶灵骑士对其他神牌的功能有免疫力」. The gun fires
#: at dawn when the night's deaths are announced, so it is not a night effect.
#:
#: Exempting by *cause* rather than by "which settlement call is this" matters:
#: a gunshot triggered by a night death is settled by the response window's
#: follow-up batch, which runs inside the night pipeline — a phase flag would
#: have misread it as a night effect and swallowed it.
#:
#: The gun is the only one the official text names, but the marker's own wording
#: is what decides the rest: 「无法**在夜间**出局」. The daytime causes reach
#: ``settle()`` through the day windows, so they are not night effects either.
#: (Only the gun is observable on an official board — the Evil Knight boards
#: carry no Knight and no White Wolf King.)
_NIGHT_IMMUNE_EXEMPT_CAUSES = frozenset({
    "hunter_shot", "knight_duel", "self_explode",
})
# Causes whose lethal hit leaves a visible poisoned / wounded mark. Anything
# else (a wolf kill) kills the delayable seat on the spot, as the rules require.
_POISON_CAUSES = frozenset({"poison"})
_WOUND_CAUSES = frozenset({"hunter_shot"})
_DELAY_CAUSES = _POISON_CAUSES | _WOUND_CAUSES
#: The Demon Hunter's mark: a hunt always lands on the *next* day, whoever it
#: hits, and the hunter cannot mark its target itself (the target is not the one
#: declaring ``delayable``). So this cause delays on its own.
HUNTED_STATUS = "hunted"
HUNT_CAUSE = "hunt"
#: The Crimson Envoy's exile reprieve: the exile stands, but the death lands
#: after the next day's speeches instead of on the spot.
LINGERING_EXILE_STATUS = "lingering_exile"
#: Causes that delay *any* seat, even one declaring no ``delayable`` mark. The
#: poison and the gunshot are deliberately absent: those still need the seat's
#: own mark, so the Old Drunkard keeps dying on the spot to everything else.
_ALWAYS_DELAYED_CAUSES = frozenset({HUNT_CAUSE})
_DELAYED_DEATH_CAUSES = {
    POISONED_STATUS: "poison",
    WOUNDED_STATUS: "hunter_shot",
    HUNTED_STATUS: HUNT_CAUSE,
    LINGERING_EXILE_STATUS: "exile",
}


def delayed_death_statuses(cause: object) -> tuple[str, ...]:
    """Statuses a lethal ``cause`` leaves on a delayable seat, if any."""
    if cause in _POISON_CAUSES:
        return (POISONED_STATUS, DELAYED_DEATH_STATUS)
    if cause in _WOUND_CAUSES:
        return (WOUNDED_STATUS, DELAYED_DEATH_STATUS)
    if cause == HUNT_CAUSE:
        return (HUNTED_STATUS, DELAYED_DEATH_STATUS)
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


#: Masquerade pool marks. ``in_pool`` sits on the three seats the Dancer chose
#: for that night, ``masked`` on the seat the Mask flipped for the comparison.
DANCE_POOL_STATUS = "in_pool"
MASKED_STATUS = "masked"
DANCE_CAUSE = "dance"


def settle_pool(
    statuses: Mapping[int, object], camps: Mapping[int, str],
    alive: Mapping[int, bool], round_number: int,
) -> tuple[tuple[dict[str, object], ...], dict[int, tuple[str, ...]]]:
    """Resolve the masquerade pool: the minority camp among the pooled seats dies.

    A masked seat counts as the opposite camp *for this comparison only*. The
    mask never changes the seat's real camp, so it cannot move the win check,
    the seer's reading or any other camp-based rule.

    The three seats were all alive when they were chosen, and the wolf kill and
    the witch's vial are settled in this very call, so the comparison uses the
    pool exactly as it was formed: a member knifed tonight still counts, and its
    death is merged (once) by the caller.
    """
    if not isinstance(statuses, Mapping) or not isinstance(camps, Mapping):
        raise TypeError("statuses and camps must be mappings")
    if not isinstance(alive, Mapping):
        raise TypeError("alive must be a mapping")
    if type(round_number) is not int or not 0 <= round_number <= _INT32:
        raise ValueError("round_number out of range")
    held_by: dict[int, frozenset[object]] = {}
    cleared: dict[int, tuple[str, ...]] = {}
    for seat, held in statuses.items():
        if type(seat) is not int or seat <= 0:
            raise ValueError("invalid status seat")
        frozen = frozenset(held if isinstance(held, (set, frozenset, tuple, list)) else ())
        held_by[seat] = frozen
        stale = tuple(
            status for status in (DANCE_POOL_STATUS, MASKED_STATUS)
            if status in frozen
        )
        if stale:
            cleared[seat] = stale
    pooled = sorted(
        seat for seat, frozen in held_by.items()
        if DANCE_POOL_STATUS in frozen and alive.get(seat, False)
    )
    if len(pooled) != 3:
        return (), cleared

    def effective_camp(seat: int) -> str:
        camp = camps.get(seat)
        if type(camp) is not str:
            raise ValueError("invalid camp for pooled seat")
        if MASKED_STATUS in held_by[seat]:
            return "werewolf" if camp == "good" else "good"
        return camp

    good = [seat for seat in pooled if effective_camp(seat) == "good"]
    wolves = [seat for seat in pooled if effective_camp(seat) != "good"]
    if not good or not wolves:
        return (), cleared
    losers = wolves if len(wolves) < len(good) else good
    deaths = tuple(
        {"seat": seat, "cause": DANCE_CAUSE, "round_number": round_number}
        for seat in losers
    )
    return deaths, cleared


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
    *,
    substitutions: list[dict[str, int]] | None = None,
) -> tuple[tuple[dict[str, object], ...], dict[int, bool], dict[int, tuple[str, ...]]]:
    """Fold pending damage and protection into this round's deaths.

    A seat that declares the ``delayable`` resource and takes a lethal hit from
    a poisoning or wounding cause survives the settlement: it is reported in the
    third return value together with the statuses the caller must apply, and
    dies on the next day instead.

    A seat that declares ``stand_in`` and takes a lethal hit is replaced: the
    seat named by that resource dies in its place (a direct death, so no shield
    can save it) and the victim survives. Passing ``substitutions`` records who
    replaced whom so the caller can spend the markers in the same commit; the
    redirect itself happens either way.
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
    damage_totals = {}
    wolf_totals, other_totals = {}, {}
    protection_by_source: dict[int, dict[str | None, int]] = {}
    causes: dict[int, str] = {}
    first_wolf_cause: dict[int, str] = {}
    first_other_cause: dict[int, str] = {}
    # The non-night slice of the non-wolf total, tracked apart so an immune seat
    # can be judged per damage class instead of as one lump.
    exempt_totals: dict[int, int] = {}
    first_exempt_cause: dict[int, str] = {}
    damage_causes: dict[int, set[str]] = {}
    unreplaceable: set[int] = set()
    for record in damage:
        target, amount = _record(
            record, seats, frozenset({"target", "amount", "cause"}),
            frozenset({"source", "unreplaceable"}),
        )
        cause = record["cause"]
        if type(cause) is not str or not cause or len(cause) > 128 or any(
            not (char.isalnum() or char in "_.:-") for char in cause
        ): raise ValueError("invalid pending damage cause")
        if "unreplaceable" in record and type(record["unreplaceable"]) is not bool:
            raise ValueError("invalid unreplaceable mark")
        # A hit the rules call unreplaceable ignores the stand-in entirely: this
        # is how the Awakened Hunter's night shot beats the Awakened Wolf
        # Beauty's illusion.
        if record.get("unreplaceable") is True:
            unreplaceable.add(target)
        _add(damage_totals, target, amount)
        causes.setdefault(target, cause)
        damage_causes.setdefault(target, set()).add(cause)
        if cause == "wolf_kill":
            _add(wolf_totals, target, amount)
            first_wolf_cause.setdefault(target, cause)
        else:
            _add(other_totals, target, amount)
            first_other_cause.setdefault(target, cause)
            if cause in _NIGHT_IMMUNE_EXEMPT_CAUSES:
                _add(exempt_totals, target, amount)
                first_exempt_cause.setdefault(target, cause)
    for record in protection:
        target, amount = _record(record, seats, frozenset({"target", "amount"}))
        # Amounts are kept per source, not pooled: a dream weave and a guard
        # shield both sit on the same seat and only one of them stops the vial.
        source = _protection_source(record)
        bucket = protection_by_source.setdefault(target, {})
        total = bucket.get(source, 0) + amount
        if total > _INT32: raise ValueError("pending amount overflow")
        bucket[source] = total
    resulting_alive = dict(alive); deaths = []; delayed: dict[int, tuple[str, ...]] = {}
    redirected: dict[int, int] = {}
    for target in sorted(damage_totals):
        shields = protection_by_source.get(target, {})
        named = {source for source in shields if source is not None}
        # An unnamed shield keeps the historical guard semantics.
        wolf_shields = sum(
            amount for source, amount in shields.items()
            if source is None or source in _WOLF_ONLY_SOURCES
        )
        any_shields = sum(
            amount for source, amount in shields.items()
            if source is not None and source in _ANY_DAMAGE_SOURCES
        )
        double_save = (
            "wolf_kill" in damage_causes[target]
            and _DOUBLE_SAVE_SOURCES.issubset(named)
        )
        lethal_wolf = (
            double_save
            or wolf_totals.get(target, 0) > wolf_shields + any_shields
        )
        lethal_other = other_totals.get(target, 0) > any_shields
        if not resulting_alive[target] or not (lethal_wolf or lethal_other):
            continue
        if lethal_wolf and lethal_other:
            cause = causes[target]
        elif lethal_wolf:
            cause = first_wolf_cause[target]
        else:
            cause = first_other_cause[target]
        if role_resources.get(target, {}).get(NIGHT_IMMUNE_RESOURCE, 0) > 0:
            # Permanent immunity against *night* effects: no death and no
            # delayed mark either. Checked before the delayable branch so a seat
            # holding both markers still takes nothing from the settlement.
            #
            # Judged per damage class rather than as one lump: the knife is
            # always a night effect, and the non-wolf side only counts the
            # exempt slice. A seat knifed *and* shot in one settlement therefore
            # still dies — to the gun.
            lethal_wolf = False
            lethal_other = exempt_totals.get(target, 0) > any_shields
            if not lethal_other:
                continue
            cause = first_exempt_cause[target]
        if cause == "poison" and (
            role_resources.get(target, {}).get(POISON_IMMUNE_RESOURCE, 0) > 0
        ):
            # Judged per cause, unlike the blanket night immunity: the vial
            # breaks on the mark, the knife does not.
            continue
        if (
            role_resources.get(target, {}).get(DELAYABLE_RESOURCE, 0) > 0
            or cause in _ALWAYS_DELAYED_CAUSES
        ):
            # The seat survives this settlement and dies on the next day; the
            # caller applies the returned marks so the delay is observable.
            # Two routes lead here: the seat declares ``delayable`` itself (the
            # Old Drunkard), or the cause always lands a day later (a hunt).
            held = delayed_death_statuses(cause)
            if held:
                delayed[target] = held
                continue
        substitute = _stand_in_substitute(target, role_resources, alive, unreplaceable)
        if substitute is not None:
            # The victim survives this hit and the stand-in dies in its place.
            # The replacement is settled after the loop so a stand-in who was
            # also a target tonight dies exactly once, with the cause that
            # explains why.
            redirected[target] = substitute
            continue
        resulting_alive[target] = False
        deaths.append({"seat": target, "cause": cause, "round_number": round_number})
    if redirected:
        replaced = set(redirected.values())
        deaths = [death for death in deaths if death["seat"] not in replaced]
        for victim, substitute in sorted(redirected.items()):
            resulting_alive[substitute] = False
            deaths.append({
                "seat": substitute, "cause": STAND_IN_CAUSE,
                "round_number": round_number,
            })
            if substitutions is not None:
                substitutions.append({"victim": victim, "substitute": substitute})
    return tuple(deaths), resulting_alive, delayed


def _stand_in_substitute(
    target: int, role_resources: Mapping[int, Mapping[str, int]],
    alive: Mapping[int, bool], unreplaceable: set[int],
) -> int | None:
    """The seat that dies in ``target``'s place, or ``None`` when no skill fires.

    Three gates, all read from declarations rather than from a role name: the
    hit must be replaceable, the seat must hold ``stand_in``, and the named seat
    must still be alive when the settlement starts — a charm whose target
    already died tonight replaces nobody.
    """
    if target in unreplaceable:
        return None
    substitute = role_resources.get(target, {}).get(STAND_IN_RESOURCE, 0)
    if type(substitute) is not int or substitute <= 0 or substitute == target:
        return None
    return substitute if alive.get(substitute) is True else None


def _record(record: object, seats: set[int], fields: frozenset[str],
            extra: frozenset[str] = frozenset({"source"})) -> tuple[int, int]:
    """Read one pending record: ``fields`` are required, ``extra`` are optional.

    Damage records may carry the ``unreplaceable`` mark and shields a ``source``;
    anything else is a malformed record and stays fatal.
    """
    if not isinstance(record, Mapping) or not fields.issubset(record) or frozenset(record) - fields - extra:
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
    if source not in _PROTECTION_SOURCES:
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
