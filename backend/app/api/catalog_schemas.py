from typing import Literal

from pydantic import BaseModel, Field


class RoleCatalogItem(BaseModel):
    role_id: str
    display_name: str
    name_zh: str
    name_en: str | None = None
    camp: str
    icon: str
    description: str
    description_en: str | None = None
    rules: list[str] = Field(default_factory=list)
    rules_en: list[str] | None = None
    max_count: int | None


class RoleCatalogResponse(BaseModel):
    roles: list[RoleCatalogItem]


class PresetItem(BaseModel):
    id: str
    name: str
    name_en: str | None = None
    description: str
    description_en: str | None = None
    role_counts: dict[str, int]
    enable_sheriff: bool = False
    # Rule-set knob the wizard forwards verbatim into ``CreateGameRequest``.
    # The Jiamian boards allow a first-night self-save; NetEase's tournament
    # boards forbid it outright.
    witch_self_save: Literal["first_night_only", "never"] = "first_night_only"
    victory: Literal["edge", "city"] = "edge"
    # Wall-clock budget the benchmark wizard seeds for this field: a twelve
    # player game needs roughly 136 model calls against 100-111 for nine or
    # ten, which is the difference between finishing and timing out.
    recommended_timeout_seconds: int = 3600


class PresetsResponse(BaseModel):
    presets: list[PresetItem]


class ConstraintsResponse(BaseModel):
    min_players: int
    max_players: int
    min_werewolves: int
    min_good: int
