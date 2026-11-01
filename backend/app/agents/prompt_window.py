"""Cache-friendly history windows for model prompts.

Providers cache prompt prefixes: a request only reuses the tokens it shares
verbatim with an earlier request from the first token on. A sliding window
(``items[-30:]``) moves its first record on every append, so the history block
never matches twice. These windows move their start only in whole steps, so
between two jumps every new prompt extends the previous one.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

T = TypeVar("T")


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def stable_tail(items: Sequence[T], minimum: int, step: int) -> list[T]:
    """Keep a tail of ``minimum`` to ``minimum + step - 1`` items.

    Never fewer than ``minimum`` (when that many exist), so the window holds at
    least what a plain ``items[-minimum:]`` did; the start only advances when
    the count crosses ``minimum + k * step``.
    """
    if minimum < 0 or step <= 0:
        raise ValueError("window needs minimum >= 0 and step > 0")
    start = max(0, (len(items) - minimum) // step * step)
    return list(items[start:])


def stable_tail_by_budget(
    lines: Sequence[str], budget: int, step: int, *,
    separator: str = "\n", measure: Callable[[str], int] = _utf8_size,
) -> list[str]:
    """Drop whole steps of the oldest lines until the joined text fits ``budget``.

    Returns an empty list when even the newest step does not fit; the caller
    keeps its own hard cut for that case.
    """
    if step <= 0:
        raise ValueError("step must be positive")
    start = 0
    while start < len(lines) and measure(separator.join(lines[start:])) > budget:
        start += step
    return list(lines[start:])
