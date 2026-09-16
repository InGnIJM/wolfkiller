from types import SimpleNamespace

import pytest

from app.catalog import (
    FIELD_CONSTRAINTS, ROLE_METADATA, STANDARD_PRESETS,
    _catalog_item, role_catalog,
)
from app.api.catalog_schemas import (
    ConstraintsResponse, PresetItem, PresetsResponse, RoleCatalogItem,
)
from app.roles.registry import builtin_registry


def test_catalog_item_known_role_uses_metadata():
    spec = SimpleNamespace(
        display_name="Werewolf", camp_id="werewolf", min_count=0,
        max_count=None, dependencies=frozenset(), exclusions=frozenset(),
    )
    item = _catalog_item("wolf-killer-werewolf", spec)

    assert item["name_zh"] == "狼人"
    assert item["icon"] == "wolf"
    assert item["camp"] == "werewolf"
    assert item["description"] == ROLE_METADATA["wolf-killer-werewolf"]["description"]


def test_catalog_item_unknown_role_falls_back_to_spec_display():
    spec = SimpleNamespace(
        display_name="Mystery", camp_id="third_party", min_count=1,
        max_count=3, dependencies=frozenset({"a"}), exclusions=frozenset(),
    )
    item = _catalog_item("x-unknown-role", spec)

    assert item["name_zh"] == "Mystery"
    assert item["icon"] == "unknown"
    assert item["description"] == ""
    assert item["min_count"] == 1
    assert item["max_count"] == 3
    assert item["dependencies"] == ["a"]
    assert item["exclusions"] == []


def test_catalog_item_empty_display_falls_back_to_role_id():
    spec = SimpleNamespace(
        display_name="", camp_id="good", min_count=0, max_count=None,
        dependencies=frozenset(), exclusions=frozenset(),
    )
    item = _catalog_item("x-unknown-role", spec)
    assert item["display_name"] == "x-unknown-role"


def test_role_catalog_covers_all_registered_roles():
    items = role_catalog(builtin_registry.freeze())

    assert len(items) == 8
    ids = {item["role_id"] for item in items}
    assert {"wolf-killer-guard", "wolf-killer-idiot", "wolf-killer-werewolf-king"} <= ids
    by_id = {item["role_id"]: item for item in items}
    assert by_id["wolf-killer-werewolf"]["name_zh"] == "狼人"
    assert by_id["wolf-killer-werewolf"]["camp"] == "werewolf"
    assert by_id["wolf-killer-guard"]["camp"] == "good"
    assert by_id["wolf-killer-idiot"]["camp"] == "good"
    assert by_id["wolf-killer-idiot"]["max_count"] == 1
    assert by_id["wolf-killer-werewolf-king"]["camp"] == "werewolf"
    assert by_id["wolf-killer-werewolf-king"]["max_count"] == 1


def test_presets_cover_nine_ten_and_twelve_player_fields():
    assert len(STANDARD_PRESETS) == 4
    by_id = {p["id"]: p for p in STANDARD_PRESETS}
    nine, ten = by_id["nine-player-standard"], by_id["ten-player-standard"]
    idiot, king = by_id["twelve-player-idiot"], by_id["twelve-player-wolf-king"]
    assert sum(nine["role_counts"].values()) == 9
    assert sum(ten["role_counts"].values()) == 10
    assert ten["role_counts"]["wolf-killer-guard"] == 1
    assert sum(idiot["role_counts"].values()) == 12
    assert idiot["role_counts"]["wolf-killer-werewolf"] == 4
    assert idiot["role_counts"]["wolf-killer-idiot"] == 1
    assert sum(king["role_counts"].values()) == 12
    assert king["role_counts"]["wolf-killer-werewolf"] == 3
    assert king["role_counts"]["wolf-killer-werewolf-king"] == 1
    assert king["role_counts"]["wolf-killer-guard"] == 1


def test_field_constraints_defaults():
    assert FIELD_CONSTRAINTS["min_players"] == 4
    assert FIELD_CONSTRAINTS["max_players"] == 12
    assert FIELD_CONSTRAINTS["min_werewolves"] == 1
    assert FIELD_CONSTRAINTS["min_good"] == 1


def test_schema_models_accept_catalog_shapes():
    item = RoleCatalogItem(**role_catalog(builtin_registry.freeze())[0])
    assert item.role_id
    preset = PresetItem(**STANDARD_PRESETS[0])
    assert preset.id
    constraints = ConstraintsResponse(**FIELD_CONSTRAINTS)
    assert constraints.min_players == 4


import pytest


@pytest.mark.asyncio
async def test_list_roles_endpoint_returns_eight_roles():
    from app.api.routes import catalog_routes

    response = await catalog_routes.list_roles()

    assert len(response.roles) == 8
    assert response.roles[0].role_id


@pytest.mark.asyncio
async def test_list_presets_endpoint_returns_standards():
    from app.api.routes import catalog_routes

    response = await catalog_routes.list_presets()

    assert len(response.presets) == 4
    assert response.presets[0].name == "九人标准场"


@pytest.mark.asyncio
async def test_get_constraints_endpoint_matches_constants():
    from app.api.routes import catalog_routes

    response = await catalog_routes.get_constraints()

    assert response.min_players == FIELD_CONSTRAINTS["min_players"]
    assert response.max_players == FIELD_CONSTRAINTS["max_players"]
