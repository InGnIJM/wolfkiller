from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from typing import Any

import pytest

from app.persistence.repository import GameRepository
from app.services.review_service import (
    ReviewGameNotFoundError,
    ReviewService,
    _default_auto_review_enabled,
)


@dataclass
class _FakeStructured:
    payload: dict[str, Any]


class _FakeReviewClient:
    def __init__(self, fail: Exception | None = None, model_name: str = "review-model"):
        self.fail = fail
        self.model_name = model_name
        self.closed = False
        self.tools: list[str] = []

    async def ainvoke_json(self, messages, *, tool_name: str, schema: dict[str, Any]):
        self.tools.append(tool_name)
        if self.fail is not None:
            raise self.fail
        if tool_name == "submit_round_digest":
            return _FakeStructured({"digest": "本轮夜间狼刀3号，白天放逐2号。"})
        return _FakeStructured({
            "overview": "好人逻辑清晰，首轮放逐真狼。",
            "mvp": {"seat": 1, "role": "预言家", "reason": "首夜验狼并带队"},
            "turning_points": [{"round": 1, "title": "警下归票", "detail": "2号狼人出局"}],
            "mistakes": [{"seat": 2, "title": "悍跳破绽", "detail": "发言前后矛盾"}],
            "camp_assessment": {"good": "抱团准确", "werewolf": "生存空间被压缩"},
            "closing": "好人完胜。",
        })

    async def aclose(self) -> None:
        self.closed = True


def _seed_finished_game(
    repository: GameRepository,
    game_id: str = "g-rev",
    *,
    source: str = "native",
    config_id: str | None = None,
) -> None:
    repository.create_game(
        game_id=game_id,
        name="rev",
        config={"commentary": {"enabled": False, "commentary_config_id": config_id}},
        execution_status="completed",
        source=source,
        model_snapshot=[],
    )
    repository.commit_step(
        game_id=game_id,
        expected_storage_revision=0,
        execution_generation=1,
        step_key="s-1",
        input_digest="i",
        result_digest="r",
        checkpoint={"checkpoint_version": 1},
        domain_events=[],
        audience_events=[
            {
                "event_id": "e-1",
                "event_type": "speech",
                "payload": {"round_number": 1, "player_seat": 1, "text": "查杀2号", "phase": "speech"},
                "schema_version": 1,
            },
            {
                "event_id": "e-2",
                "event_type": "winner",
                "payload": {"winning_camp": "good", "reason": "all_wolves_dead"},
                "schema_version": 1,
            },
        ],
        audience_state={
            "players": {
                "1": {"seat_number": 1, "role": "wolf-killer-seer", "camp": "good", "is_alive": True},
                "2": {"seat_number": 2, "role": "wolf-killer-werewolf", "camp": "werewolf", "is_alive": False},
            },
            "win_result": {"winning_camp": "good", "reason": "all_wolves_dead"},
        },
    )


@pytest.mark.asyncio
async def test_review_service_lifecycle_persistence_and_force_regenerate(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    clients: list[_FakeReviewClient] = []

    def factory(_cfg):
        c = _FakeReviewClient()
        clients.append(c)
        return c

    service = ReviewService(
        repository=repository,
        data_dir=str(tmp_path),
        client_factory=factory,
        auto_review_enabled_getter=lambda: True,
    )
    try:
        with pytest.raises(ReviewGameNotFoundError):
            service.get_review("no-such-game")
        with pytest.raises(ReviewGameNotFoundError):
            await service.request_review("no-such-game")

        _seed_finished_game(repository, "g-rev", source="native")
        assert service.get_review("g-rev")["status"] == "none"

        # Request review -> pending -> wait -> ready
        res = await service.request_review("g-rev")
        assert res["status"] == "pending"
        # Calling request_review again while pending returns pending without starting second task
        res_dup = await service.request_review("g-rev")
        assert res_dup["status"] == "pending"

        await service.wait_pending()
        ready = service.get_review("g-rev")
        assert ready["status"] == "ready"
        assert ready["review"]["overview"] == "好人逻辑清晰，首轮放逐真狼。"
        assert ready["review"]["mvp"]["seat"] == 1
        assert len(clients) == 1
        assert clients[0].closed is True

        # Non-forced request returns existing ready review without calling LLM again
        cached = await service.request_review("g-rev", force=False)
        assert cached["status"] == "ready"
        assert len(clients) == 1

        # Forced request regenerates
        forced = await service.request_review("g-rev", force=True)
        assert forced["status"] == "pending"
        await service.wait_pending()
        assert len(clients) == 2
        assert service.get_review("g-rev")["status"] == "ready"
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_review_service_on_game_over_auto_and_benchmark_skip(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    called = 0

    def factory(_cfg):
        nonlocal called
        called += 1
        return _FakeReviewClient()

    auto_flag = True
    service = ReviewService(
        repository=repository,
        data_dir=str(tmp_path),
        client_factory=factory,
        auto_review_enabled_getter=lambda: auto_flag,
    )
    try:
        # Benchmark game should not auto-review
        _seed_finished_game(repository, "g-bench", source="benchmark")
        await service.on_game_over(game_id="g-bench")
        await service.on_game_over(game_id=None)
        await service.on_game_over(game_id="missing")
        await service.wait_pending()
        assert called == 0

        # Auto-review disabled -> skip native game too
        auto_flag = False
        _seed_finished_game(repository, "g-native-off", source="native")
        await service.on_game_over(game_id="g-native-off")
        await service.wait_pending()
        assert called == 0

        # Auto-review enabled -> runs on native game
        auto_flag = True
        _seed_finished_game(repository, "g-native-on", source="native")
        await service.on_game_over(game_id="g-native-on")
        await service.wait_pending()
        assert called == 1
        assert service.get_review("g-native-on")["status"] == "ready"
    finally:
        await service.aclose()
        repository.close()


@pytest.mark.asyncio
async def test_review_service_failure_corrupt_file_and_cancel(tmp_path) -> None:
    repository = GameRepository(tmp_path)
    try:
        _seed_finished_game(repository, "g-fail", source="native")
        fail_service = ReviewService(
            repository=repository,
            data_dir=str(tmp_path),
            client_factory=lambda _: _FakeReviewClient(fail=RuntimeError("llm down")),
            auto_review_enabled_getter=lambda: False,
        )
        await fail_service.request_review("g-fail")
        await fail_service.wait_pending()
        failed = fail_service.get_review("g-fail")
        assert failed["status"] == "failed"
        assert failed["failure"] is not None

        # Corrupt review.json reads as status='none'
        corrupt_dir = tmp_path / "games" / "g-fail"
        corrupt_dir.mkdir(parents=True, exist_ok=True)
        (corrupt_dir / "review.json").write_text("{corrupt", encoding="utf-8")
        assert fail_service.get_review("g-fail")["status"] == "none"

        # Non-dict JSON in review.json reads as status='none'
        (corrupt_dir / "review.json").write_text(json.dumps([1, 2]), encoding="utf-8")
        assert fail_service.get_review("g-fail")["status"] == "none"

        # Cancel in-flight task via aclose() and force-replace in-flight task
        started = asyncio.Event()

        class _SlowReviewClient(_FakeReviewClient):
            async def ainvoke_json(self, messages, *, tool_name: str, schema: dict[str, Any]):
                started.set()
                await asyncio.sleep(10)  # pragma: no cover - cancelled before wake
                return _FakeStructured({"digest": "x"})  # pragma: no cover

        slow_service = ReviewService(
            repository=repository,
            data_dir=str(tmp_path),
            client_factory=lambda _: _SlowReviewClient(),
            auto_review_enabled_getter=lambda: False,
        )
        await slow_service.request_review("g-fail")
        await asyncio.wait_for(started.wait(), timeout=2.0)
        # Force while in-flight cancels the first task and starts a new one
        started.clear()
        await slow_service.request_review("g-fail", force=True)
        await asyncio.wait_for(started.wait(), timeout=2.0)
        await slow_service.aclose()
        # Request after aclose is a no-op task spawn
        await slow_service.request_review("g-fail", force=True)
        await slow_service.wait_pending()

        # Default auto_review_enabled getter reads app settings store
        assert isinstance(_default_auto_review_enabled(), bool)

        # Factory raising before client is constructed
        def broken_factory(_cfg):
            raise RuntimeError("broken factory")

        broken_service = ReviewService(
            repository=repository,
            data_dir=str(tmp_path),
            client_factory=broken_factory,
        )
        await broken_service.request_review("g-fail", force=True)
        await broken_service.wait_pending()
        assert broken_service.get_review("g-fail")["status"] == "failed"
    finally:
        repository.close()
