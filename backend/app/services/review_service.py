"""Post-game two-stage LLM review service (round digests -> structured review)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.commentary_prompt import (
    COMMENTARY_SYSTEM_PROMPT,
    REVIEW_SCHEMA,
    ROUND_DIGEST_SCHEMA,
    build_review_prompt,
    build_round_digest_prompt,
    format_roster,
    format_transcript,
    normalize_review_payload,
)
from app.agents.failure_taxonomy import normalize_failure_code
from app.agents.llm_client import LLMClientConfig
from app.persistence.repository import GameRepository
from app.services.commentary_service import (
    _default_client_factory,
    extract_roster_and_roles,
    fetch_all_audience_events,
    resolve_commentary_client_config,
)
from app.stores.app_settings_store import (
    get_app_settings_store,
    resolve_auto_review_setting,
)

logger = logging.getLogger(__name__)
REVIEW_FILENAME = "review.json"
_ROUND_CONCURRENCY = 3


class ReviewGameNotFoundError(Exception):
    code = "game_not_found"


def _default_auto_review_enabled() -> bool:
    enabled, _ = resolve_auto_review_setting(
        get_app_settings_store().get_auto_review_enabled(),
    )
    return enabled


class ReviewService:
    """Manage on-disk ``review.json`` and background two-stage review generation."""

    def __init__(
        self,
        repository: GameRepository,
        data_dir: str = "data",
        *,
        client_factory: Callable[[LLMClientConfig], Any] = _default_client_factory,
        auto_review_enabled_getter: Callable[[], bool] = _default_auto_review_enabled,
    ) -> None:
        self._repository = repository
        self._data_dir = data_dir
        self._client_factory = client_factory
        self._auto_review_enabled = auto_review_enabled_getter
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    def _review_path(self, game_id: str) -> Path:
        return Path(self._data_dir) / "games" / game_id / REVIEW_FILENAME

    def _require_game(self, game_id: str) -> dict[str, object]:
        game = self._repository.get_game(game_id)
        if game is None:
            raise ReviewGameNotFoundError(game_id)
        return game

    def _read_disk_review(self, game_id: str) -> dict[str, Any] | None:
        path = self._review_path(game_id)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    def _write_disk_review(self, game_id: str, payload: dict[str, Any]) -> None:
        path = self._review_path(game_id)
        os.makedirs(path.parent, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def get_review(self, game_id: str) -> dict[str, Any]:
        self._require_game(game_id)
        in_flight = self._tasks.get(game_id)
        if in_flight is not None and not in_flight.done():
            return {
                "game_id": game_id,
                "status": "pending",
                "review": None,
                "failure": None,
                "generated_at": None,
                "model_id": None,
            }
        stored = self._read_disk_review(game_id)
        if stored is None or stored.get("status") not in ("ready", "failed"):
            return {
                "game_id": game_id,
                "status": "none",
                "review": None,
                "failure": None,
                "generated_at": None,
                "model_id": None,
            }
        return {
            "game_id": game_id,
            "status": str(stored["status"]),
            "review": stored.get("review"),
            "failure": stored.get("failure"),
            "generated_at": stored.get("generated_at"),
            "model_id": stored.get("model_id"),
        }

    async def request_review(
        self, game_id: str, *, force: bool = False,
    ) -> dict[str, Any]:
        game = self._require_game(game_id)
        in_flight = self._tasks.get(game_id)
        if in_flight is not None and not in_flight.done():
            if not force:
                return self.get_review(game_id)
            in_flight.cancel()
            await asyncio.gather(in_flight, return_exceptions=True)
            self._tasks.pop(game_id, None)
        elif not force:
            current = self.get_review(game_id)
            if current["status"] == "ready":
                return current

        if not self._closed:
            task = asyncio.create_task(self._generate(game_id, game))
            self._tasks[game_id] = task
        return {
            "game_id": game_id,
            "status": "pending",
            "review": None,
            "failure": None,
            "generated_at": None,
            "model_id": None,
        }

    async def on_game_over(self, **kwargs: Any) -> None:
        game_id = kwargs.get("game_id")
        if not isinstance(game_id, str) or not game_id:
            return
        game = self._repository.get_game(game_id)
        if game is None or game.get("source") != "native":
            return
        if not self._auto_review_enabled():
            return
        await self.request_review(game_id, force=False)

    async def wait_pending(self) -> None:
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def aclose(self) -> None:
        self._closed = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _generate(self, game_id: str, game: Mapping[str, object]) -> None:
        cfg = game.get("config")
        comm = cfg.get("commentary") if isinstance(cfg, Mapping) else None
        raw_cid = comm.get("commentary_config_id") if isinstance(comm, Mapping) else None
        config_id = raw_cid if isinstance(raw_cid, str) and raw_cid else None
        raw_mid = comm.get("commentary_model_id") if isinstance(comm, Mapping) else None
        model_id = raw_mid if isinstance(raw_mid, str) and raw_mid else None
        raw_eff = comm.get("commentary_reasoning_effort") if isinstance(comm, Mapping) else None
        effort = raw_eff if isinstance(raw_eff, str) and raw_eff else None
        now = datetime.now(timezone.utc).isoformat()
        client = None
        try:
            events = await asyncio.to_thread(
                fetch_all_audience_events, self._repository, game_id,
            )
            players, roles_by_seat = await asyncio.to_thread(
                extract_roster_and_roles, self._repository, game_id,
            )
            roster_text = format_roster(players)
            by_round: dict[int, list[dict[str, object]]] = {}
            outcome_text = "未知结局"
            for ev in events:
                pl = ev["payload"]
                if ev.get("event_type") == "winner":
                    outcome_text = f"{pl.get('winning_camp')} 获胜（{pl.get('reason')}）"
                rnd = pl.get("round_number")
                r_key = rnd if type(rnd) is int and rnd >= 1 else 1
                by_round.setdefault(r_key, []).append(ev)

            client = self._client_factory(resolve_commentary_client_config(
                config_id,
                model_id=model_id,
                reasoning_effort=effort,
            ))
            model_id = getattr(client, "model_name", "unknown")
            sem = asyncio.Semaphore(_ROUND_CONCURRENCY)

            async def summarize_round(rnd: int, rnd_events: list[dict[str, object]]) -> tuple[int, str]:
                transcript = format_transcript(rnd_events, roles_by_seat)
                prompt = build_round_digest_prompt(
                    round_number=rnd,
                    roster_text=roster_text,
                    transcript_text=transcript,
                )
                async with sem:
                    res = await client.ainvoke_json(
                        [
                            SystemMessage(content=COMMENTARY_SYSTEM_PROMPT),
                            HumanMessage(content=prompt),
                        ],
                        tool_name="submit_round_digest",
                        schema=ROUND_DIGEST_SCHEMA,
                    )
                return rnd, str(res.payload.get("digest") or "").strip()

            round_pairs = await asyncio.gather(*[
                summarize_round(rnd, by_round[rnd]) for rnd in sorted(by_round)
            ])
            round_digests = [
                {"round": rnd, "digest": text} for rnd, text in round_pairs
            ]
            review_prompt = build_review_prompt(
                roster_text=roster_text,
                outcome_text=outcome_text,
                round_digests=round_pairs,
            )
            final_res = await client.ainvoke_json(
                [
                    SystemMessage(content=COMMENTARY_SYSTEM_PROMPT),
                    HumanMessage(content=review_prompt),
                ],
                tool_name="submit_game_review",
                schema=REVIEW_SCHEMA,
            )
            review_obj = normalize_review_payload(
                final_res.payload, round_digests=round_digests,
            )
            record = {
                "game_id": game_id,
                "status": "ready",
                "review": review_obj,
                "failure": None,
                "generated_at": now,
                "model_id": model_id,
            }
            await asyncio.to_thread(self._write_disk_review, game_id, record)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Post-game review failed for game=%s: %s", game_id, error)
            record = {
                "game_id": game_id,
                "status": "failed",
                "review": None,
                "failure": normalize_failure_code(error),
                "generated_at": now,
                "model_id": getattr(client, "model_name", "unknown") if client else "unknown",
            }
            await asyncio.to_thread(self._write_disk_review, game_id, record)
        finally:
            if client is not None:
                await client.aclose()
