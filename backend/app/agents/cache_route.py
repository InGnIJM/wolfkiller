"""Session-route pinning for routing prompt-cache gateways.

OpenCode Go routes each ``x-opencode-session`` to an upstream backend and the
prefix cache lives on that backend. Some sessions land on a backend without a
cache, so an identical prompt reports 0 cached tokens for the whole game (the
2026-11-04 run: 8 of 48 seats were 0% while their neighbours hit 60-72%). This
experiment probes each routing seat with a fixed prompt, rotates the session
when the backend is cold, and records the result in
``data/games/<id>/cache_route.jsonl`` for the A/B analysis.

The switch is process configuration: it never enters GameConfig, checkpoints,
PointKey or the model snapshot. Probe calls bypass the durable ledger and the
``llm_calls.log`` telemetry on purpose -- they are diagnostics, not game turns.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from enum import StrEnum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.providers.base import (
    CacheMode, ProviderProfile, new_session_id,
)
from app.agents.session_affinity import treatment_seats

logger = logging.getLogger(__name__)

#: Process-level experiment switch. ``off`` (default) / ``on`` / ``ab``.
CACHE_ROUTE_ENV = "WOLFKILLER_CACHE_ROUTE_PIN"

#: Process-level switch for concentrating routing-cache seats on one
#: upstream session. ``off`` (default) / ``on``. No ``ab``: sharing is a
#: game-global property, so arms can only be compared across games.
SHARED_SESSION_ENV = "WOLFKILLER_SHARED_CACHE_SESSION"

#: Process-level switch for running the first ballot of a vote phase alone
#: so the parallel rest can reuse its freshly committed prompt prefix.
#: ``off`` (default) / ``on``. Meaningful only for shared-session games:
#: followers on other upstream backends gain nothing and just pay the
#: extra latency of one serial call.
BURST_WARMUP_ENV = "WOLFKILLER_BURST_WARMUP"

#: One probe = two identical calls; the second one is the measurement.
PROBE_TIMEOUT_SECONDS = 5.0
PROBE_MAX_ATTEMPTS = 2
PIN_TOTAL_TIMEOUT_SECONDS = 30.0
PIN_CONCURRENCY = 4

#: DeepSeek reports cache hits in 64-token blocks; anything smaller is noise.
MIN_CACHED_TOKENS = 64

#: Large enough to cross the gateway's 64-token cache block even on a model
#: with a short tokenizer, small enough to stay cheap next to a real turn.
_PROBE_SYSTEM = "## cache route probe\n" + "\n".join(
    f"- probe line {i}: stable text for the routing cache probe; do not answer it."
    for i in range(1, 120)
)
_PROBE_USER = "Reply with OK."


class CacheRouteMode(StrEnum):
    """How (and whether) routing-cache sessions are probed and rotated."""

    OFF = "off"
    ON = "on"
    AB = "ab"


class SharedSessionMode(StrEnum):
    """Whether routing-cache seats share one upstream session."""

    OFF = "off"
    ON = "on"


class BurstWarmupMode(StrEnum):
    """Whether the first vote of a casting phase runs before the rest."""

    OFF = "off"
    ON = "on"


def shared_session_mode(env_value: str | None = None) -> SharedSessionMode:
    """Resolve the shared-session switch; same fail-loud contract as the pin."""
    raw = os.getenv(SHARED_SESSION_ENV) if env_value is None else env_value
    if raw is None or not raw.strip():
        return SharedSessionMode.OFF
    try:
        return SharedSessionMode(raw.strip().lower())
    except ValueError:
        raise ValueError(
            f"{SHARED_SESSION_ENV} must be one of "
            f"{', '.join(mode.value for mode in SharedSessionMode)} "
            f"(got {raw!r})"
        ) from None


def burst_warmup_mode(env_value: str | None = None) -> BurstWarmupMode:
    """Resolve the vote-warmup switch; same fail-loud contract as the pin."""
    raw = os.getenv(BURST_WARMUP_ENV) if env_value is None else env_value
    if raw is None or not raw.strip():
        return BurstWarmupMode.OFF
    try:
        return BurstWarmupMode(raw.strip().lower())
    except ValueError:
        raise ValueError(
            f"{BURST_WARMUP_ENV} must be one of "
            f"{', '.join(mode.value for mode in BurstWarmupMode)} "
            f"(got {raw!r})"
        ) from None


def shared_session_id(game_id: str, session_header: str) -> str:
    """Deterministic per-(game, gateway) session id in the client's shape.

    Derived from the game id so a process restart or a resumed game lands the
    seats back on the same upstream backend -- possibly still warm. The
    ``ses_`` + 26-hex shape matches ``new_session_id`` so relays that validate
    the format accept it.
    """
    digest = hashlib.sha256(
        f"wk-shared:{session_header}:{game_id}".encode("utf-8")
    ).hexdigest()
    return f"ses_{digest[:26]}"


def _routing_session_header(client: Any) -> str | None:
    """The session header of a routing-cache client, or None for others."""
    profile = getattr(client, "provider_profile", None)
    if getattr(profile, "cache_mode", CacheMode.UNKNOWN) != CacheMode.ROUTING:
        return None
    return getattr(profile, "session_header", None)


async def apply_shared_session(
    clients: dict[int, Any], *, game_id: str, mode: SharedSessionMode,
) -> list[int]:
    """Point every routing-cache client at one per-(game, gateway) session.

    Groups by the profile's session header so a hypothetical game spanning
    two routing gateways shares one id per gateway, not one globally.
    Returns the affected seats for logging; ``off`` is a no-op.
    """
    if mode is SharedSessionMode.OFF:
        return []
    groups: dict[str, list[tuple[int, Any]]] = {}
    for seat, client in clients.items():
        header = _routing_session_header(client)
        if header:
            groups.setdefault(header, []).append((seat, client))
    applied: list[int] = []
    for header, members in groups.items():
        session = shared_session_id(game_id, header)
        for seat, client in members:
            await client.set_session_id(session)
            applied.append(seat)
    return sorted(applied)


def cache_route_mode(env_value: str | None = None) -> CacheRouteMode:
    """Resolve the experiment switch.

    Unset or blank means ``off``; an operator typo fails when the next game is
    created rather than silently mixing experiment arms.
    """
    raw = os.getenv(CACHE_ROUTE_ENV) if env_value is None else env_value
    if raw is None or not raw.strip():
        return CacheRouteMode.OFF
    try:
        return CacheRouteMode(raw.strip().lower())
    except ValueError:
        raise ValueError(
            f"{CACHE_ROUTE_ENV} must be one of "
            f"{', '.join(mode.value for mode in CacheRouteMode)} (got {raw!r})"
        ) from None


def should_probe(
    profile: ProviderProfile,
    *,
    game_id: str,
    seat: int,
    total_players: int,
    mode: CacheRouteMode,
) -> bool:
    """Only routing providers, only treatment seats, only sessions we own."""
    if mode is CacheRouteMode.OFF:
        return False
    if getattr(profile, "cache_mode", CacheMode.UNKNOWN) != CacheMode.ROUTING:
        return False
    if not getattr(profile, "session_header", None):
        return False
    if mode is CacheRouteMode.AB:
        return seat in treatment_seats(game_id, total_players)
    return True


def probe_messages() -> list:
    """Stable two-message probe; long enough for a cache block to register."""
    return [
        SystemMessage(content=_PROBE_SYSTEM),
        HumanMessage(content=_PROBE_USER),
    ]


async def probe_and_pin(
    client: Any,
    *,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    attempts: int = PROBE_MAX_ATTEMPTS,
) -> dict[str, Any]:
    """Probe one seat's session, rotating it when the backend is cold."""
    result: dict[str, Any] = {"attempts": 0, "hot": False, "cached": None}
    for attempt in range(1, attempts + 1):
        result["attempts"] = attempt
        try:
            cached = await client.probe_cache(probe_messages(), timeout=timeout)
        except Exception as error:  # noqa: BLE001 - a failed probe must not block
            logger.warning(
                "cache route probe failed on attempt %s: %s", attempt, error,
            )
            cached = None
        result["cached"] = cached
        if cached is not None and cached >= MIN_CACHED_TOKENS:
            result["hot"] = True
            return result
        if attempt < attempts:
            try:
                await client.reset_session()
            except Exception as error:  # noqa: BLE001
                logger.warning("cache session reset failed: %s", error)
                return result
    return result


async def probe_and_pin_group(
    clients: list[Any],
    *,
    timeout: float = PROBE_TIMEOUT_SECONDS,
    attempts: int = PROBE_MAX_ATTEMPTS,
) -> dict[str, Any]:
    """Probe one session group; the representative answers for every member.

    A cold group rotates together: all members move to the same fresh id so
    they keep sharing one backend instead of scattering to solo sessions.
    """
    representative = clients[0]
    result: dict[str, Any] = {"attempts": 0, "hot": False, "cached": None}
    for attempt in range(1, attempts + 1):
        result["attempts"] = attempt
        try:
            cached = await representative.probe_cache(
                probe_messages(), timeout=timeout,
            )
        except Exception as error:  # noqa: BLE001 - a failed probe must not block
            logger.warning(
                "cache route probe failed on attempt %s: %s", attempt, error,
            )
            cached = None
        result["cached"] = cached
        if cached is not None and cached >= MIN_CACHED_TOKENS:
            result["hot"] = True
            return result
        if attempt < attempts:
            new_id = new_session_id()
            try:
                for client in clients:
                    await client.set_session_id(new_id)
            except Exception as error:  # noqa: BLE001
                logger.warning("cache session reset failed: %s", error)
                return result
    return result


async def pin_cache_routes(
    clients: dict[int, Any],
    *,
    game_id: str,
    total_players: int,
    mode: CacheRouteMode,
    total_timeout: float = PIN_TOTAL_TIMEOUT_SECONDS,
    concurrency: int = PIN_CONCURRENCY,
) -> dict[int, dict[str, Any]]:
    """Probe every targeted seat under a bounded budget.

    Seats sharing one ``session_id`` form a single probe group: the group's
    representative is probed once and a cold group rotates together.
    Returns ``{seat: result}``. An empty dict means no seat was targeted or
    the probe timed out; callers treat this as best-effort telemetry, never
    as a game precondition.
    """
    targets = [
        seat
        for seat, client in clients.items()
        if should_probe(
            client.provider_profile,
            game_id=game_id,
            seat=seat,
            total_players=total_players,
            mode=mode,
        )
    ]
    if not targets:
        return {}
    groups: dict[str, list[int]] = {}
    for seat in targets:
        key = getattr(clients[seat], "session_id", None) or f"__solo__:{seat}"
        groups.setdefault(key, []).append(seat)
    semaphore = asyncio.Semaphore(concurrency)

    async def probe_group(seats: list[int]) -> tuple[list[int], dict[str, Any]]:
        async with semaphore:
            try:
                if len(seats) == 1:
                    result = await probe_and_pin(clients[seats[0]])
                else:
                    result = await probe_and_pin_group(
                        [clients[seat] for seat in seats],
                    )
            except Exception as error:  # noqa: BLE001
                logger.warning(
                    "cache route pin failed for seats %s: %s", seats, error,
                )
                result = {
                    "attempts": 0, "hot": False, "cached": None,
                    "error": type(error).__name__,
                }
            result["group_size"] = len(seats)
            return seats, result

    try:
        pairs = await asyncio.wait_for(
            asyncio.gather(*(probe_group(seats) for seats in groups.values())),
            timeout=total_timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("cache route probe timed out for game %s", game_id)
        return {}
    return {
        seat: dict(result)
        for seats, result in pairs
        for seat in seats
    }
