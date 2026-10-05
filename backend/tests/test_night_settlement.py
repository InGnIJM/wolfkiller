import pytest

from app.core.night_settlement import _protection_source, settle


def test_guard_and_witch_saving_the_same_wolf_target_causes_double_save_death() -> None:
    deaths, alive, delayed = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},),
        (
            {"target": 2, "amount": 1, "source": "guard"},
            {"target": 2, "amount": 1, "source": "witch_antidote"},
        ),
        {1, 2},
        {1: True, 2: True},
        1,
    )

    assert deaths == ({"seat": 2, "cause": "wolf_kill", "round_number": 1},)
    assert alive == {1: True, 2: False}
    assert delayed == {}


def test_one_protection_still_blocks_a_wolf_kill() -> None:
    deaths, alive, delayed = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},),
        ({"target": 2, "amount": 1, "source": "guard"},),
        {1, 2},
        {1: True, 2: True},
        1,
    )

    assert deaths == ()
    assert alive == {1: True, 2: True}
    assert delayed == {}


def test_double_save_penetrates_when_wolf_kill_is_not_the_first_damage() -> None:
    deaths, alive, delayed = settle(
        (
            {"target": 2, "amount": 1, "cause": "poison"},
            {"target": 2, "amount": 1, "cause": "wolf_kill"},
        ),
        (
            {"target": 2, "amount": 1, "source": "guard"},
            {"target": 2, "amount": 1, "source": "witch_antidote"},
        ),
        {1, 2},
        {1: True, 2: True},
        1,
    )

    assert deaths == ({"seat": 2, "cause": "poison", "round_number": 1},)
    assert alive == {1: True, 2: False}
    assert delayed == {}


def test_guard_does_not_block_poison() -> None:
    deaths, alive, delayed = settle(
        ({"target": 2, "amount": 1, "cause": "poison"},),
        ({"target": 2, "amount": 1, "source": "guard"},),
        {1, 2},
        {1: True, 2: True},
        1,
    )

    assert deaths == ({"seat": 2, "cause": "poison", "round_number": 1},)
    assert alive == {1: True, 2: False}
    assert delayed == {}


def test_witch_antidote_does_not_block_poison() -> None:
    deaths, alive, delayed = settle(
        ({"target": 2, "amount": 1, "cause": "poison"},),
        ({"target": 2, "amount": 1, "source": "witch_antidote"},),
        {1, 2},
        {1: True, 2: True},
        1,
    )

    assert deaths == ({"seat": 2, "cause": "poison", "round_number": 1},)
    assert alive == {1: True, 2: False}
    assert delayed == {}


def test_guarded_wolf_kill_plus_poison_dies_of_poison() -> None:
    deaths, alive, delayed = settle(
        (
            {"target": 2, "amount": 1, "cause": "wolf_kill"},
            {"target": 2, "amount": 1, "cause": "poison"},
        ),
        ({"target": 2, "amount": 1, "source": "guard"},),
        {1, 2},
        {1: True, 2: True},
        1,
    )

    assert deaths == ({"seat": 2, "cause": "poison", "round_number": 1},)
    assert alive == {1: True, 2: False}
    assert delayed == {}


def test_guard_does_not_block_hunter_shot() -> None:
    deaths, alive, delayed = settle(
        ({"target": 2, "amount": 1, "cause": "hunter_shot"},),
        ({"target": 2, "amount": 1, "source": "guard"},),
        {1, 2},
        {1: True, 2: True},
        1,
    )

    assert deaths == ({"seat": 2, "cause": "hunter_shot", "round_number": 1},)
    assert alive == {1: True, 2: False}
    assert delayed == {}


def test_protection_source_validation_rejects_invalid_records() -> None:
    with pytest.raises(ValueError, match="pending effect"):
        _protection_source(object())
    with pytest.raises(ValueError, match="pending effect"):
        settle((), (object(),), {1}, {1: True}, 1)
    with pytest.raises(ValueError, match="protection source"):
        settle((), ({"target": 1, "amount": 1, "source": "unknown"},), {1}, {1: True}, 1)


# ── protection tiers (§2.3) ───────────────────────────────────────────────

def test_a_dream_weave_blocks_the_poison_where_a_guard_cannot() -> None:
    """The whole point of the tier split: the vial ignores a plain guard."""
    for source, expected in (("guard", 1), ("dream", 0)):
        deaths, alive, _ = settle(
            ({"target": 2, "amount": 1, "cause": "poison"},),
            ({"target": 2, "amount": 1, "source": source},),
            {1, 2}, {1: True, 2: True}, 1,
        )
        assert len(deaths) == expected, source
        assert alive[2] is (expected == 0)


def test_a_dream_weave_also_blocks_the_knife() -> None:
    deaths, alive, _ = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},),
        ({"target": 2, "amount": 1, "source": "dream"},),
        {1, 2}, {1: True, 2: True}, 1,
    )
    assert deaths == () and alive[2] is True


def test_a_dream_weave_does_not_count_towards_the_double_save_breach() -> None:
    """Only guard and antidote breach; a dream plus an antidote still saves."""
    deaths, alive, _ = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},),
        (
            {"target": 2, "amount": 1, "source": "dream"},
            {"target": 2, "amount": 1, "source": "witch_antidote"},
        ),
        {1, 2}, {1: True, 2: True}, 1,
    )
    assert deaths == () and alive[2] is True


def test_a_dream_weave_cannot_save_a_target_the_wolves_double_up_on() -> None:
    """One shield stops one hit; two hits still land."""
    deaths, alive, _ = settle(
        ({"target": 2, "amount": 2, "cause": "wolf_kill"},),
        ({"target": 2, "amount": 1, "source": "dream"},),
        {1, 2}, {1: True, 2: True}, 1,
    )
    assert len(deaths) == 1 and alive[2] is False


def test_an_unnamed_protection_keeps_the_historical_guard_semantics() -> None:
    """Old records carry no source; they must still stop the knife, not the vial."""
    blocked, alive, _ = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},),
        ({"target": 2, "amount": 1},),
        {1, 2}, {1: True, 2: True}, 1,
    )
    assert blocked == () and alive[2] is True
    poisoned, alive, _ = settle(
        ({"target": 2, "amount": 1, "cause": "poison"},),
        ({"target": 2, "amount": 1},),
        {1, 2}, {1: True, 2: True}, 1,
    )
    assert len(poisoned) == 1 and alive[2] is False


def test_an_unnamed_protection_never_completes_a_double_save() -> None:
    """It behaves like a guard for damage, but it is not a *named* guard."""
    deaths, alive, _ = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},),
        (
            {"target": 2, "amount": 1},
            {"target": 2, "amount": 1, "source": "witch_antidote"},
        ),
        {1, 2}, {1: True, 2: True}, 1,
    )
    assert deaths == () and alive[2] is True


def test_a_dream_weave_and_a_guard_still_block_a_double_hit() -> None:
    """Two shields from two tiers cover two points of damage."""
    deaths, alive, _ = settle(
        ({"target": 2, "amount": 2, "cause": "wolf_kill"},),
        (
            {"target": 2, "amount": 1, "source": "guard"},
            {"target": 2, "amount": 1, "source": "dream"},
        ),
        {1, 2}, {1: True, 2: True}, 1,
    )
    assert deaths == () and alive[2] is True


def test_protection_amount_overflow_is_rejected() -> None:
    with pytest.raises(ValueError):
        settle(
            ({"target": 2, "amount": 1, "cause": "wolf_kill"},),
            (
                {"target": 2, "amount": 2_147_483_647, "source": "guard"},
                {"target": 2, "amount": 1, "source": "guard"},
            ),
            {1, 2}, {1: True, 2: True}, 1,
        )


def test_silenced_status_is_read_from_plain_sets() -> None:
    """封印标记的读取路径：普通 set 与 frozenset 等价，缺座位即未封印。"""
    from app.core.night_settlement import (
        NIGHT_SCOPED_STATUSES, SILENCED_STATUS, is_silenced,
    )

    assert SILENCED_STATUS in NIGHT_SCOPED_STATUSES
    assert is_silenced({1: {"silenced"}, 2: {"poisoned"}}, 1) is True
    assert is_silenced({1: frozenset({"silenced"})}, 1) is True
    assert is_silenced({1: {"poisoned"}}, 1) is False
    assert is_silenced({}, 1) is False
    assert is_silenced({1: ()}, 1) is False


def test_the_hunt_cause_delays_any_seat_not_only_a_marked_one() -> None:
    """猎魔人不能给目标加标记，所以 ``hunt`` 本身就要走延迟档。

    与老酒鬼的 ``delayable`` 分开：那条路要求目标自带资源标记，而这条只看死因。
    """
    from app.core.night_settlement import (
        DELAYED_DEATH_STATUS, HUNTED_STATUS, _ALWAYS_DELAYED_CAUSES, settle,
    )

    assert "hunt" in _ALWAYS_DELAYED_CAUSES
    # Poison and the gunshot still need the seat's own ``delayable`` mark.
    assert "poison" not in _ALWAYS_DELAYED_CAUSES
    assert "hunter_shot" not in _ALWAYS_DELAYED_CAUSES
    damage = ({"target": 2, "amount": 1, "cause": "hunt"},)
    deaths, alive, delayed = settle(
        damage, (), {1, 2}, {1: True, 2: True}, round_number=3,
    )
    assert deaths == ()
    assert alive[2] is True
    assert delayed == {2: (HUNTED_STATUS, DELAYED_DEATH_STATUS)}


def test_delayed_death_statuses_cover_the_hunt_mark() -> None:
    from app.core.night_settlement import (
        DELAYED_DEATH_STATUS, HUNTED_STATUS, delayed_death_statuses,
    )

    assert delayed_death_statuses("hunt") == (HUNTED_STATUS, DELAYED_DEATH_STATUS)
    assert delayed_death_statuses("wolf_kill") == ()
    assert delayed_death_statuses("exile") == ()


def test_delayed_deaths_land_the_hunt_cause_on_the_next_day() -> None:
    from app.core.night_settlement import (
        DELAYED_DEATH_STATUS, HUNTED_STATUS, resolve_delayed_deaths,
    )

    deaths, cleared = resolve_delayed_deaths(
        {2: {HUNTED_STATUS, DELAYED_DEATH_STATUS}}, 3,
    )
    assert deaths == ({"seat": 2, "cause": "hunt", "round_number": 3},)
    assert cleared == {2: (DELAYED_DEATH_STATUS, HUNTED_STATUS)}


def test_a_plain_seat_still_dies_to_the_knife_on_the_same_night() -> None:
    """延迟档只覆盖 hunt / 中毒 / 中枪：夜刀与放逐仍然当夜出局。"""
    from app.core.night_settlement import settle

    deaths, alive, delayed = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},),
        (), {1, 2}, {1: True, 2: True}, round_number=2,
    )
    assert [(item["seat"], item["cause"]) for item in deaths] == [(2, "wolf_kill")]
    assert alive[2] is False
    assert delayed == {}
