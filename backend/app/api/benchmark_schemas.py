from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


BenchmarkMode = Literal["mixed_arena", "paired_regression"]
BenchmarkStatus = Literal[
    "draft", "pending", "running", "pausing", "paused", "completed",
    "cancelled", "interrupted", "blocked", "failed",
]


class BenchmarkCreateRequest(BaseModel):
    """Public draft request; unknown fields are retained in the frozen spec."""

    model_config = ConfigDict(extra="allow")

    client_request_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    mode: BenchmarkMode
    seed: int = Field(ge=0)
    scenario: dict[str, Any] | None = None
    role_counts: dict[str, int] | None = None
    block_count: int = Field(default=1, ge=1, le=10_000)
    max_games: int | None = Field(default=None, ge=1)
    concurrency: int = Field(default=1, ge=1, le=4)
    game_timeout_seconds: int = Field(default=3600, ge=1)
    max_attempts_per_game: int | None = Field(default=None, ge=1)

    # Compatibility fields understood by the current deterministic scheduler.
    games: int | None = Field(default=None, ge=1)
    repetitions: int | None = Field(default=None, ge=1)
    models: list[dict[str, Any]] | None = None
    model_assignments: list[dict[str, Any]] | None = None
    baseline: dict[str, Any] | None = None
    candidate: dict[str, Any] | None = None
    baseline_config_id: str | None = None
    candidate_config_id: str | None = None

    @field_validator("name", "client_request_id")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    def frozen_specification(self) -> dict[str, Any]:
        spec = self.model_dump(
            exclude={"client_request_id"}, exclude_none=True,
        )
        if "scenario" not in spec and self.role_counts is not None:
            spec["scenario"] = {
                "scenario_id": "default",
                "role_counts": dict(self.role_counts),
            }

        counts = self.role_counts
        if counts is None and isinstance(self.scenario, dict):
            candidate_counts = self.scenario.get("role_counts")
            counts = candidate_counts if isinstance(candidate_counts, dict) else None
        player_count = sum(counts.values()) if counts else 0
        if self.mode == "mixed_arena":
            if "models" not in spec and self.model_assignments is not None:
                assignments: list[tuple[dict[str, Any], int]] = []
                for item in self.model_assignments:
                    count = item.get("count", 1)
                    if type(count) is not int or count < 1:
                        raise ValueError("model assignment count must be positive")
                    model = {key: value for key, value in item.items() if key != "count"}
                    assignments.append((model, count))
                if sum(count for _, count in assignments) != player_count:
                    raise ValueError("model assignment counts must equal player count")
                spec["models"] = [
                    dict(model) for model, count in assignments for _ in range(count)
                ]
            elif self.models is not None:
                if not self.models:
                    raise ValueError("mixed arena requires model configurations")
                if len(self.models) > player_count:
                    raise ValueError("model count must not exceed player count")
                # Short lists select models; freeze a complete seat plan before
                # scheduling. Full seat lists retain their order and weighting.
                seats_per_model, extra_seats = divmod(player_count, len(self.models))
                spec["models"] = [
                    dict(model)
                    for index, model in enumerate(spec["models"])
                    for _ in range(seats_per_model + (index < extra_seats))
                ]
            if "games" not in spec:
                planned = self.block_count * max(player_count * player_count, 1)
                spec["games"] = min(planned, self.max_games) if self.max_games else planned
        else:
            if "baseline" not in spec and self.baseline_config_id:
                spec["baseline"] = {"model_config_id": self.baseline_config_id}
            if "candidate" not in spec and self.candidate_config_id:
                spec["candidate"] = {"model_config_id": self.candidate_config_id}
            if spec.get("baseline") == spec.get("candidate") and spec.get("baseline") is not None:
                raise ValueError("baseline and candidate configurations must differ")
            if "repetitions" not in spec:
                pairs = self.block_count
                if self.max_games is not None:
                    if self.max_games % 2:
                        raise ValueError("paired regression max_games must be even")
                    pairs = min(pairs, self.max_games // 2)
                spec["repetitions"] = pairs
        return spec


class BenchmarkRunResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    client_request_id: str
    name: str
    mode: BenchmarkMode
    status: str
    config: dict[str, Any]
    schedule_digest: str
    created_at: str
    updated_at: str
    planned_count: int = 0
    started_count: int = 0
    terminal_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    progress: float = 0.0
    replayed: bool | None = None


class BenchmarkListResponse(BaseModel):
    benchmarks: list[BenchmarkRunResponse]
    offset: int
    limit: int
    total: int


class BenchmarkGameResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    run_id: str
    item_index: int
    scenario_id: str
    pair_id: str | None
    block_index: int
    assignment: dict[str, Any]
    game_id: str | None
    status: str
    terminal_reason: str | None
    created_at: str
    updated_at: str
    name: str | None = None
    phase: str | None = None
    round_number: int | None = None
    player_count: int | None = None
    alive_count: int | None = None
    winner: str | None = None
    execution_status: str | None = None


class BenchmarkGamesResponse(BaseModel):
    games: list[BenchmarkGameResponse]
    offset: int
    limit: int
    total: int
