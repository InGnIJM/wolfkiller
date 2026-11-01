"""Asynchronous side-channel service for live god-view commentary."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timezone
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.commentary_prompt import (
    COMMENTARY_SCHEMA,
    COMMENTARY_SYSTEM_PROMPT,
    beat_title,
    build_segment_prompt,
    format_roster,
    format_transcript,
)
from app.agents.failure_taxonomy import normalize_failure_code
from app.agents.llm_client import (
    LLMClient,
    LLMClientConfig,
    derive_strict_base_url,
    env_default_client_config,
)
from app.core.game_logger import GameLogger
from app.persistence.repository import GameRepository
from app.stores.model_config_store import get_model_config_store
from app.stores.model_key_crypto import KeyDecryptionError, ModelKeyCrypto

logger = logging.getLogger(__name__)


def resolve_commentary_client_config(config_id: str | None) -> LLMClientConfig:
    """Resolve an LLMClientConfig for commentary/review from a saved config or .env."""
    env_cfg = replace(env_default_client_config(), provider_profile="auto")
    if config_id is None:
        return env_cfg
    config = get_model_config_store().get(config_id)
    if config is None:
        raise ValueError("unknown model config")
    if config.api_key_encrypted:
        try:
            api_key = ModelKeyCrypto().decrypt(config.api_key_encrypted)
        except KeyDecryptionError:
            raise ValueError("model config api key is invalid") from None
    else:
        api_key = env_cfg.api_key
    return LLMClientConfig(
        base_url=config.base_url,
        api_key=api_key,
        model_id=config.model_id,
        temperature=(
            config.temperature
            if config.temperature is not None
            else env_cfg.temperature
        ),
        max_tokens=env_cfg.max_tokens,
        action_max_tokens=env_cfg.action_max_tokens,
        strict_base_url=derive_strict_base_url(
            config.base_url, config.strict_base_url,
        ),
        action_timeout_seconds=env_cfg.action_timeout_seconds,
        action_retry_timeout_seconds=env_cfg.action_retry_timeout_seconds,
        action_final_retry_timeout_seconds=env_cfg.action_final_retry_timeout_seconds,
        provider_profile=config.provider_profile,
        headers=tuple(config.headers.items()),
    )


def _default_client_factory(config_id: str | None) -> LLMClient:
    # One-shot clients are closed after every segment/review; they must own
    # private httpx pools or that aclose() kills the running game's seats.
    return LLMClient(
        config=resolve_commentary_client_config(config_id),
        dedicated_http_clients=True,
    )


def fetch_all_audience_events(
    repository: GameRepository, game_id: str, *, after_seq: int = 0,
    page_limit: int = 500,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    cursor = after_seq
    while True:
        page = repository.get_audience_events(game_id, after_seq=cursor, limit=page_limit)
        events.extend(page["events"])
        next_seq = page["next_seq"]
        if not page["has_more"] or next_seq <= cursor:
            break
        cursor = int(next_seq)
    return events


def extract_roster_and_roles(
    repository: GameRepository, game_id: str,
) -> tuple[dict[str, Any], dict[int, str]]:
    snap = repository.get_audience_snapshot(game_id)
    state = snap.get("state") if isinstance(snap, Mapping) else None
    players = state.get("players") if isinstance(state, Mapping) else None
    if not isinstance(players, Mapping):
        return {}, {}
    roles_by_seat: dict[int, str] = {}
    for info in players.values():
        if isinstance(info, Mapping):
            seat = info.get("seat_number")
            role = info.get("role")
            if type(seat) is int and isinstance(role, str):
                roles_by_seat[seat] = role
    return dict(players), roles_by_seat


class CommentaryService:
    """Side-channel listener that writes audience-only commentary rows."""

    def __init__(
        self,
        repository: GameRepository,
        ws_manager: Any,
        data_dir: str = "data",
        *,
        client_factory: Callable[[str | None], Any] = _default_client_factory,
        settle_delay_seconds: float = 0.5,
    ) -> None:
        self._repository = repository
        self._ws_manager = ws_manager
        self._data_dir = data_dir
        self._client_factory = client_factory
        self._settle_delay = settle_delay_seconds
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    def _spawn(self, coro) -> None:
        if self._closed:
            coro.close()
            return
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def wait_pending(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def aclose(self) -> None:
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def on_phase_changed(self, **kwargs: Any) -> None:
        game_id = kwargs.get("game_id")
        phase = kwargs.get("phase")
        round_number = kwargs.get("round_number")
        if (
            not isinstance(game_id, str)
            or not game_id
            or not isinstance(phase, str)
            or type(round_number) is not int
        ):
            return
        if phase in ("dawn", "sheriff_election") and round_number >= 1:
            beat = "night"
        elif phase == "night" and round_number >= 1:
            beat = "day"
        else:
            return
        self._spawn(self._run_beat(game_id, round_number, beat, phase))

    async def on_game_over(self, **kwargs: Any) -> None:
        game_id = kwargs.get("game_id")
        if not isinstance(game_id, str) or not game_id:
            return
        self._spawn(self._run_final(game_id))

    def _commentary_config(self, game_id: str) -> tuple[bool, str | None, int]:
        game = self._repository.get_game(game_id)
        if game is None:
            return False, None, 1
        cfg = game.get("config")
        comm = cfg.get("commentary") if isinstance(cfg, Mapping) else None
        if not isinstance(comm, Mapping) or comm.get("enabled") is not True:
            return False, None, 1
        cid = comm.get("commentary_config_id")
        config_id = cid if isinstance(cid, str) and cid else None
        snap = self._repository.get_audience_snapshot(game_id)
        state = snap.get("state") if isinstance(snap, Mapping) else None
        rnd = state.get("round_number") if isinstance(state, Mapping) else 1
        return True, config_id, (rnd if type(rnd) is int and rnd >= 1 else 1)

    async def _run_final(self, game_id: str) -> None:
        enabled, _, round_number = self._commentary_config(game_id)
        if not enabled:
            return
        await self._run_beat(game_id, round_number, "final", "game_over")

    async def _run_beat(
        self, game_id: str, round_number: int, beat: str, phase: str,
    ) -> None:
        if self._settle_delay > 0:
            await asyncio.sleep(self._settle_delay)
        lock = self._locks.setdefault(game_id, asyncio.Lock())
        async with lock:
            enabled, config_id, _ = self._commentary_config(game_id)
            if not enabled:
                return
            all_events = await asyncio.to_thread(
                fetch_all_audience_events, self._repository, game_id,
            )
            cursor_seq = 0
            for ev in all_events:
                if ev.get("event_type") == "commentary":
                    cursor_seq = max(cursor_seq, int(ev["seq"]))
                    pl = ev.get("payload")
                    if (
                        isinstance(pl, Mapping)
                        and pl.get("round_number") == round_number
                        and pl.get("beat") == beat
                    ):
                        return
            slice_events = [
                ev for ev in all_events
                if int(ev["seq"]) > cursor_seq
            ]
            players, roles_by_seat = await asyncio.to_thread(
                extract_roster_and_roles, self._repository, game_id,
            )
            transcript = format_transcript(slice_events, roles_by_seat)
            if transcript == "（本段无事件记录）":
                return
            roster_text = format_roster(players)
            user_prompt = build_segment_prompt(
                round_number=round_number,
                beat=beat,
                roster_text=roster_text,
                transcript_text=transcript,
            )
            title = beat_title(round_number, beat)
            now = datetime.now(timezone.utc).isoformat()
            client = None
            try:
                client = self._client_factory(config_id)
                model_id = getattr(client, "model_name", "unknown")
                messages = [
                    SystemMessage(content=COMMENTARY_SYSTEM_PROMPT),
                    HumanMessage(content=user_prompt),
                ]
                res = await client.ainvoke_json(
                    messages, tool_name="submit_commentary", schema=COMMENTARY_SCHEMA,
                )
                text = str(res.payload.get("text") or "").strip()
                payload: dict[str, object] = {
                    "round_number": round_number,
                    "beat": beat,
                    "title": title,
                    "text": text or "（解说生成为空）",
                    "model_id": model_id,
                    "generated_at": now,
                    "status": "ok",
                }
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.warning("Commentary generation failed for game=%s: %s", game_id, error)
                payload = {
                    "round_number": round_number,
                    "beat": beat,
                    "title": title,
                    "text": "（本段解说生成失败）",
                    "model_id": getattr(client, "model_name", "unknown") if client else "unknown",
                    "generated_at": now,
                    "status": "failed",
                    "failure_code": normalize_failure_code(error),
                }
            finally:
                if client is not None:
                    await client.aclose()

            event_id = f"commentary:{round_number}:{beat}"
            await self._repository.append_audience_events_async(
                game_id,
                [{
                    "event_id": event_id,
                    "event_type": "commentary",
                    "payload": payload,
                    "schema_version": 1,
                    "timestamp": now,
                }],
            )
            await asyncio.to_thread(
                GameLogger(self._data_dir).log_audience_action,
                game_id, round_number, phase, "COMMENTARY", payload,
            )
            if self._ws_manager is not None:
                self._ws_manager.wake_v2(game_id)
