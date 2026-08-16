from pydantic import BaseModel


class RoleCatalogItem(BaseModel):
    role_id: str
    display_name: str
    name_zh: str
    camp: str
    icon: str
    description: str
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


class PresetsResponse(BaseModel):
    presets: list[PresetItem]


class ConstraintsResponse(BaseModel):
    min_players: int
    max_players: int
    min_werewolves: int
    min_good: int
