import pytest

from app.agents.prompt_window import stable_tail, stable_tail_by_budget


def test_short_sequences_are_kept_whole():
    assert stable_tail([], 3, 2) == []
    assert stable_tail([1, 2, 3, 4], 3, 2) == [1, 2, 3, 4]


def test_window_is_a_tail_between_minimum_and_minimum_plus_step():
    for size in range(0, 70):
        items = list(range(size))
        window = stable_tail(items, 30, 10)
        assert min(size, 30) <= len(window) < 40
        assert window == items[size - len(window):]


def test_window_start_only_moves_in_whole_steps():
    starts = {stable_tail(list(range(size)), 30, 10)[0] for size in range(30, 70)}
    assert starts == {0, 10, 20, 30}


def test_appending_inside_a_step_keeps_the_old_window_as_a_prefix():
    before = stable_tail(list(range(45)), 30, 10)
    after = stable_tail(list(range(49)), 30, 10)
    assert after[:len(before)] == before


@pytest.mark.parametrize("minimum, step", [(3, 0), (-1, 2)])
def test_invalid_window_shape_is_rejected(minimum, step):
    with pytest.raises(ValueError):
        stable_tail([1], minimum, step)


def test_budget_tail_keeps_everything_that_fits():
    lines = ["aa", "bb", "cc"]
    assert stable_tail_by_budget(lines, 8, 2) == lines
    assert stable_tail_by_budget([], 0, 2) == []


def test_budget_tail_drops_whole_steps_from_the_head():
    lines = [f"{index:02d}" for index in range(10)]
    # 10 lines x 2 bytes + 9 separators = 29 bytes; 20 bytes needs 3 dropped,
    # which rounds up to one whole step of 4.
    assert stable_tail_by_budget(lines, 20, 4) == lines[4:]


def test_budget_tail_is_stable_while_appends_still_fit():
    lines = [f"{index:02d}" for index in range(10)]
    before = stable_tail_by_budget(lines, 20, 4)
    after = stable_tail_by_budget(lines + ["10"], 20, 4)
    assert after == lines[4:] + ["10"]
    assert after[:len(before)] == before


def test_budget_tail_measures_bytes_by_default_and_accepts_a_custom_measure():
    assert stable_tail_by_budget(["狼", "人"], 5, 1) == ["人"]
    assert stable_tail_by_budget(["狼", "人"], 3, 1, measure=len) == ["狼", "人"]


def test_budget_tail_returns_nothing_when_no_line_fits():
    assert stable_tail_by_budget(["toolong"], 3, 1) == []


def test_budget_tail_rejects_a_non_positive_step():
    with pytest.raises(ValueError):
        stable_tail_by_budget(["a"], 1, 0)
