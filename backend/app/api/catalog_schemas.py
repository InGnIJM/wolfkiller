from pydantic import BaseModel, Field


class RoleCatalogItem(BaseModel):
    role_id: str
    display_name: str
    name_zh: str
    camp: str
    icon: str
    description: str
    rules: list[str] = Field(default_factory=list)
    min_count: int
    max_count: int | None
    dependencies: list[str]
    exclusions: list[str]


class RoleCatalogResponse(BaseModel):
    roles: list[RoleCatalogItem]


class PresetItem(BaseModel):
    id: str
    name: str
    description: str
    role_counts: dict[str, int]
    enable_sheriff: bool = False
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
