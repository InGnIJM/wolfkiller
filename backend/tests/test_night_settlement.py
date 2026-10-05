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
