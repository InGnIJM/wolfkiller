import pytest

from app.core.night_settlement import _protection_source, settle


def test_guard_and_witch_saving_the_same_wolf_target_causes_double_save_death() -> None:
    deaths, alive = settle(
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


def test_one_protection_still_blocks_a_wolf_kill() -> None:
    deaths, alive = settle(
        ({"target": 2, "amount": 1, "cause": "wolf_kill"},),
        ({"target": 2, "amount": 1, "source": "guard"},),
        {1, 2},
        {1: True, 2: True},
        1,
    )

    assert deaths == ()
    assert alive == {1: True, 2: True}


def test_double_save_penetrates_when_wolf_kill_is_not_the_first_damage() -> None:
    deaths, alive = settle(
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


def test_guard_does_not_block_poison() -> None:
    deaths, alive = settle(
        ({"target": 2, "amount": 1, "cause": "poison"},),
        ({"target": 2, "amount": 1, "source": "guard"},),
        {1, 2},
        {1: True, 2: True},
        1,
    )

    assert deaths == ({"seat": 2, "cause": "poison", "round_number": 1},)
    assert alive == {1: True, 2: False}


def test_witch_antidote_does_not_block_poison() -> None:
    deaths, alive = settle(
        ({"target": 2, "amount": 1, "cause": "poison"},),
        ({"target": 2, "amount": 1, "source": "witch_antidote"},),
        {1, 2},
        {1: True, 2: True},
        1,
    )

    assert deaths == ({"seat": 2, "cause": "poison", "round_number": 1},)
    assert alive == {1: True, 2: False}


def test_guarded_wolf_kill_plus_poison_dies_of_poison() -> None:
    deaths, alive = settle(
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


def test_guard_does_not_block_hunter_shot() -> None:
    deaths, alive = settle(
        ({"target": 2, "amount": 1, "cause": "hunter_shot"},),
        ({"target": 2, "amount": 1, "source": "guard"},),
        {1, 2},
        {1: True, 2: True},
        1,
    )

    assert deaths == ({"seat": 2, "cause": "hunter_shot", "round_number": 1},)
    assert alive == {1: True, 2: False}


def test_protection_source_validation_rejects_invalid_records() -> None:
    with pytest.raises(ValueError, match="pending effect"):
        _protection_source(object())
    with pytest.raises(ValueError, match="pending effect"):
        settle((), (object(),), {1}, {1: True}, 1)
    with pytest.raises(ValueError, match="protection source"):
        settle((), ({"target": 1, "amount": 1, "source": "unknown"},), {1}, {1: True}, 1)
