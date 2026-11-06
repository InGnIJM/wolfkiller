"""Per-seat session affinity for OpenAI-compatible gateways.

Antigravity-style gateways derive their upstream conversation (and therefore
prefix-cache reuse) from a session id.  Without one they fall back to a content
anchor over the first user message, which changes on every turn of a game, so
every request opens a fresh upstream session.  This module stamps a
deterministic per-seat id on the request headers behind a process-level switch
so the effect can be measured without changing anything else.

The switch is process configuration, never game state: it is read when a game
is created or resumed and is deliberately absent from ``GameConfig``,
checkpoints, ``PointKey`` and the model parameter digest.  The headers it adds
are already excluded from the model snapshot and the public record.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterable
from dataclasses import replace
from enum import StrEnum

from app.agents.llm_client import LLMClientConfig

logger = logging.getLogger(__name__)

#: Process-level experiment switch.  ``off`` (default) / ``on`` / ``ab``.
SESSION_AFFINITY_ENV = "WOLFKILLER_SESSION_AFFINITY"

#: Generic session header understood by Antigravity-style gateways.  Product
#: specific names (``x-antigravity-session-id`` and friends) take precedence on
#: the gateway side, but every relay ignores a generic one it does not know.
SESSION_HEADER_NAME = "x-session-id"

_SESSION_KEY_PREFIX = "wk"

#: Only the OpenAI-compatible gateway profile is addressed.  Other profiles
#: either have their own session header (opencode) or do not use one at all.
_GATEWAY_PROFILE_ID = "custom-openai"


class SessionAffinityMode(StrEnum):
    """How (and whether) session ids are stamped on requests."""

    OFF = "off"
    ON = "on"
    AB = "ab"


def session_affinity_mode(env_value: str | None = None) -> SessionAffinityMode:
    """Resolve the experiment switch.

    Unset or blank means ``off``.  Anything else must be one of the three mode
    names; an operator typo fails loudly when the next game is created rather
    than silently producing mixed experiment data.
    """
    raw = os.getenv(SESSION_AFFINITY_ENV) if env_value is None else env_value
    if raw is None or not raw.strip():
        return SessionAffinityMode.OFF
    try:
        return SessionAffinityMode(raw.strip().lower())
    except ValueError:
        raise ValueError(
            f"{SESSION_AFFINITY_ENV} must be one of "
            f"{', '.join(mode.value for mode in SessionAffinityMode)} "
            f"(got {raw!r})"
        ) from None


def session_key(game_id: str, seat: int) -> str:
    """Stable per-(game, seat) key; identical across calls and resumes."""
    return f"{_SESSION_KEY_PREFIX}-{game_id}-s{seat}"


def treatment_seats(game_id: str, total_players: int) -> frozenset[int]:
    """Deterministic half of the seats used as the A/B treatment arm.

    The split is derived from the game id so the report can recompute it after
    the fact, and so a benchmark whose roles are fixed per seat still rotates
    the two arms across games.
    """
    ranked = sorted(
        range(1, total_players + 1),
        key=lambda seat: hashlib.sha256(
            f"{game_id}:{seat}".encode("utf-8")
        ).digest(),
    )
    return frozenset(ranked[: total_players // 2])


def session_affinity_plan(
    game_id: str, total_players: int, mode: SessionAffinityMode,
) -> dict[str, object]:
    """Human-readable description of the arm, for logs and tests."""
    if mode is SessionAffinityMode.OFF:
        seats: tuple[int, ...] = ()
    elif mode is SessionAffinityMode.ON:
        seats = tuple(range(1, total_players + 1))
    else:
        seats = tuple(sorted(treatment_seats(game_id, total_players)))
    return {
        "mode": mode.value,
        "header": SESSION_HEADER_NAME,
        "treatment_seats": list(seats),
    }


def _is_treatment_seat(
    game_id: str, seat: int, total_players: int, mode: SessionAffinityMode,
) -> bool:
    if mode is SessionAffinityMode.ON:
        return True
    if mode is SessionAffinityMode.AB:
        return seat in treatment_seats(game_id, total_players)
    return False


def _has_session_header(headers: Iterable[tuple[str, str]]) -> bool:
    return any(name.lower() == SESSION_HEADER_NAME for name, _ in headers)


def apply_session_affinity(
    config: LLMClientConfig,
    *,
    game_id: str,
    seat: int,
    total_players: int,
    mode: SessionAffinityMode,
    profile_id: str,
) -> LLMClientConfig:
    """Return ``config`` with a stable session header when this seat is treated.

    Returns the original object unchanged otherwise, so a disabled experiment
    costs nothing and keeps the existing client-config identity.
    """
    if mode is SessionAffinityMode.OFF:
        return config
    if profile_id != _GATEWAY_PROFILE_ID:
        return config
    if not _is_treatment_seat(game_id, seat, total_players, mode):
        return config
    if _has_session_header(config.headers):
        # An explicitly configured header is the operator's choice; do not
        # silently override it, and let the report flag the seat as untreated.
        logger.warning(
            "session affinity skipped for game %s seat %s: %s already configured",
            game_id, seat, SESSION_HEADER_NAME,
        )
        return config
    return replace(
        config,
        headers=tuple(config.headers) + (
            (SESSION_HEADER_NAME, session_key(game_id, seat)),
        ),
    )
