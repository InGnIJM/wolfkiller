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
    assert item["rules"] == ROLE_METADATA["wolf-killer-werewolf"]["rules"]


def test_catalog_item_unknown_role_falls_back_to_spec_display():
    spec = SimpleNamespace(
        display_name="Mystery", camp_id="third_party", min_count=1,
        max_count=3, dependencies=frozenset({"a"}), exclusions=frozenset(),
    )
    item = _catalog_item("x-unknown-role", spec)

    assert item["name_zh"] == "Mystery"
    assert item["icon"] == "unknown"
    assert item["description"] == ""
    assert item["rules"] == []
    assert item["min_count"] == 1
    assert item["max_count"] == 3
    assert item["dependencies"] == ["a"]
    assert item["exclusions"] == []


def test_catalog_item_drops_non_string_rule_entries(monkeypatch):
    spec = SimpleNamespace(
        display_name="Mixed", camp_id="good", min_count=0,
        max_count=None, dependencies=frozenset(), exclusions=frozenset(),
    )
    monkeypatch.setitem(ROLE_METADATA, "x-mixed-role", {"rules": ["可用", 7]})

    assert _catalog_item("x-mixed-role", spec)["rules"] == ["可用"]


def test_every_registered_role_publishes_rule_bullets():
    """The rules page renders whatever the catalog lists, so a role that ships
    without player-facing rule text would show up blank there."""
    items = role_catalog(builtin_registry.freeze())

    assert all(item["name_zh"] for item in items)
    assert all(item["description"] for item in items)
    for item in items:
        assert item["rules"], item["role_id"]
        assert all(rule.strip() for rule in item["rules"]), item["role_id"]


def test_catalog_item_empty_display_falls_back_to_role_id():
    spec = SimpleNamespace(
        display_name="", camp_id="good", min_count=0, max_count=None,
        dependencies=frozenset(), exclusions=frozenset(),
    )
    item = _catalog_item("x-unknown-role", spec)
    assert item["display_name"] == "x-unknown-role"


def test_role_catalog_covers_all_registered_roles():
    items = role_catalog(builtin_registry.freeze())

    assert len(items) == 24
    ids = {item["role_id"] for item in items}
    assert {
        "wolf-killer-guard", "wolf-killer-idiot", "wolf-killer-werewolf-king",
        "wolf-killer-knight", "wolf-killer-wolf-beauty", "wolf-killer-old-drunkard",
    } <= ids
    by_id = {item["role_id"]: item for item in items}
    assert by_id["wolf-killer-werewolf"]["name_zh"] == "狼人"
    assert by_id["wolf-killer-werewolf"]["camp"] == "werewolf"
    assert by_id["wolf-killer-guard"]["camp"] == "good"
    assert by_id["wolf-killer-idiot"]["camp"] == "good"
    assert by_id["wolf-killer-idiot"]["max_count"] == 1
    assert by_id["wolf-killer-werewolf-king"]["camp"] == "werewolf"
    assert by_id["wolf-killer-werewolf-king"]["max_count"] == 1
    assert by_id["wolf-killer-knight"]["camp"] == "good"
    assert by_id["wolf-killer-knight"]["max_count"] == 1
    assert by_id["wolf-killer-wolf-beauty"]["camp"] == "werewolf"
    assert by_id["wolf-killer-wolf-beauty"]["max_count"] == 1
    assert by_id["wolf-killer-old-drunkard"]["camp"] == "good"
    assert by_id["wolf-killer-old-drunkard"]["max_count"] == 1


def test_masquerade_preset_matches_the_jiangcheng_board() -> None:
    """十二人假面舞会：3狼 1假面 4民 预女白痴 舞者；女巫保留首夜自救（非网易板）。"""
    preset = next(
        item for item in STANDARD_PRESETS if item["id"] == "twelve-player-masquerade"
    )
    assert preset["role_counts"] == {
        "wolf-killer-werewolf": 3,
        "wolf-killer-mask": 1,
        "wolf-killer-villager": 4,
        "wolf-killer-seer": 1,
        "wolf-killer-witch": 1,
        "wolf-killer-idiot": 1,
        "wolf-killer-dancer": 1,
    }
    assert sum(preset["role_counts"].values()) == 12
    assert "witch_self_save" not in preset
    assert preset["enable_sheriff"] is True
    assert preset["recommended_timeout_seconds"] == 7200


def test_nightmare_preset_matches_the_netease_tournament_board() -> None:
    """十二人噩梦之影：摄梦人替代守卫，其余是标准的神职三件套。"""
    preset = next(
        item for item in STANDARD_PRESETS if item["id"] == "twelve-player-nightmare"
    )
    assert preset["role_counts"] == {
        "wolf-killer-werewolf": 3,
        "wolf-killer-nightmare": 1,
        "wolf-killer-villager": 4,
        "wolf-killer-seer": 1,
        "wolf-killer-witch": 1,
        "wolf-killer-hunter": 1,
        "wolf-killer-dream-weaver": 1,
    }
    assert sum(preset["role_counts"].values()) == 12
    assert "wolf-killer-guard" not in preset["role_counts"]
    assert preset["enable_sheriff"] is True
    assert preset["recommended_timeout_seconds"] == 7200


def test_crimson_hunter_preset_matches_the_netease_tournament_board() -> None:
    """十二人赤月猎魔人：赤月使徒 + 猎魔人 + 白痴，没有守卫与猎人。"""
    preset = next(
        item for item in STANDARD_PRESETS if item["id"] == "twelve-player-crimson-hunter"
    )
    assert preset["role_counts"] == {
        "wolf-killer-werewolf": 3,
        "wolf-killer-crimson-envoy": 1,
        "wolf-killer-villager": 4,
        "wolf-killer-seer": 1,
        "wolf-killer-witch": 1,
        "wolf-killer-demon-hunter": 1,
        "wolf-killer-idiot": 1,
    }
    assert sum(preset["role_counts"].values()) == 12
    assert "wolf-killer-guard" not in preset["role_counts"]
    assert "wolf-killer-hunter" not in preset["role_counts"]
    assert preset["enable_sheriff"] is True
    assert preset["recommended_timeout_seconds"] == 7200


def test_pure_white_preset_matches_the_netease_tournament_board() -> None:
    """十二人纯白夜影：没有预言家，两个查验位是纯白之女与狼巫。"""
    preset = next(
        item for item in STANDARD_PRESETS if item["id"] == "twelve-player-pure-white"
    )
    assert preset["role_counts"] == {
        "wolf-killer-werewolf": 3,
        "wolf-killer-wolf-witch": 1,
        "wolf-killer-villager": 4,
        "wolf-killer-guard": 1,
        "wolf-killer-witch": 1,
        "wolf-killer-hunter": 1,
        "wolf-killer-pure-white": 1,
    }
    assert "wolf-killer-seer" not in preset["role_counts"]
    assert sum(preset["role_counts"].values()) == 12
    assert preset["enable_sheriff"] is True
    assert preset["recommended_timeout_seconds"] == 7200


def test_presets_cover_nine_ten_and_twelve_player_fields():
    assert len(STANDARD_PRESETS) == 15
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
    assert nine["enable_sheriff"] is False
    assert ten["enable_sheriff"] is False
    assert idiot["enable_sheriff"] is True
    assert king["enable_sheriff"] is True
    knight, beauty = by_id["twelve-player-knight"], by_id["twelve-player-wolf-beauty"]
    assert sum(knight["role_counts"].values()) == 12
    assert knight["role_counts"]["wolf-killer-werewolf"] == 4
    assert knight["role_counts"]["wolf-killer-knight"] == 1
    assert knight["role_counts"]["wolf-killer-villager"] == 4
    assert knight["enable_sheriff"] is False
    assert sum(beauty["role_counts"].values()) == 12
    assert beauty["role_counts"]["wolf-killer-werewolf"] == 3
    assert beauty["role_counts"]["wolf-killer-wolf-beauty"] == 1
    assert beauty["role_counts"]["wolf-killer-old-drunkard"] == 1
    assert beauty["role_counts"]["wolf-killer-guard"] == 1
    assert beauty["enable_sheriff"] is True


def test_wolf_beauty_knight_preset_matches_the_netease_tournament_board() -> None:
    """NetEase's official 12-player board: three wolves plus Wolf Beauty, four
    villagers, Seer / Witch / Guard / Knight, played with the sheriff and with
    the witch forbidden from saving herself."""
    board = next(
        p for p in STANDARD_PRESETS if p["id"] == "twelve-player-wolf-beauty-knight"
    )
    assert board["role_counts"] == {
        "wolf-killer-werewolf": 3,
        "wolf-killer-wolf-beauty": 1,
        "wolf-killer-villager": 4,
        "wolf-killer-seer": 1,
        "wolf-killer-witch": 1,
        "wolf-killer-guard": 1,
        "wolf-killer-knight": 1,
    }
    assert sum(board["role_counts"].values()) == 12
    assert board["enable_sheriff"] is True
    assert board["witch_self_save"] == "never"


def test_wolf_king_guard_preset_matches_the_netease_tournament_board() -> None:
    """NetEase's official 12-player board: three wolves plus the Wolf King, four
    villagers, Seer / Witch / Hunter / Guard."""
    board = next(
        p for p in STANDARD_PRESETS if p["id"] == "twelve-player-wolf-king-guard"
    )
    assert board["role_counts"] == {
        "wolf-killer-werewolf": 3,
        "wolf-killer-wolf-king": 1,
        "wolf-killer-villager": 4,
        "wolf-killer-seer": 1,
        "wolf-killer-witch": 1,
        "wolf-killer-hunter": 1,
        "wolf-killer-guard": 1,
    }
    assert sum(board["role_counts"].values()) == 12
    assert board["enable_sheriff"] is True
    assert board["witch_self_save"] == "never"


def test_wolf_king_and_white_wolf_king_are_distinct_role_ids() -> None:
    """The two ids differ only in word order, so pin them apart explicitly."""
    assert ROLE_METADATA["wolf-killer-wolf-king"]["name_zh"] == "狼王"
    assert ROLE_METADATA["wolf-killer-werewolf-king"]["name_zh"] == "白狼王"


def test_only_the_netease_boards_override_the_witch_self_save_policy() -> None:
    """Every other preset keeps the long-standing first-night allowance.

    The NetEase set is every preset that reproduces an official board. The
    official card text is flat: "女巫在每天晚上最多使用一瓶药，女巫不可自救" —
    there is no first-night exception, so ``twelve-player-idiot`` (the official
    12-player standard board) and ``twelve-player-wolf-king`` (the official
    12-player white-wolf-king board) must both pin ``never``.
    """
    netease = {
        "twelve-player-idiot", "twelve-player-wolf-king",
        "twelve-player-wolf-beauty-knight", "twelve-player-wolf-king-guard",
        "twelve-player-evil-knight", "twelve-player-wolf-king-dream",
        "twelve-player-gargoyle-gravekeeper", "twelve-player-pure-white",
        "twelve-player-crimson-hunter", "twelve-player-nightmare",
    }
    for preset in STANDARD_PRESETS:
        expected = "never" if preset["id"] in netease else "first_night_only"
        assert preset.get("witch_self_save", "first_night_only") == expected, preset["id"]


def test_every_preset_names_registered_roles_with_publishable_metadata() -> None:
    """预设里的角色 id 拼错只会在建局时炸；这里在目录层就把它挡住。

    同时要求每个被预设用到的角色都有 `rules` 展示条目 —— 规则页与创建向导都读它，
    漏写则新板子上线即残缺（这条门禁此前只覆盖「已注册角色」的全集）。
    """
    snapshot = builtin_registry.freeze()
    for preset in STANDARD_PRESETS:
        assert preset["role_counts"], preset["id"]
        for role_id, count in preset["role_counts"].items():
            assert role_id in snapshot.specs, (preset["id"], role_id)
            assert role_id in ROLE_METADATA, (preset["id"], role_id)
            assert count >= 1, (preset["id"], role_id)
            assert ROLE_METADATA[role_id]["rules"], (preset["id"], role_id)
            assert ROLE_METADATA[role_id]["name_zh"], (preset["id"], role_id)


def test_masquerade_roles_are_playable_through_the_frozen_snapshot() -> None:
    """假面舞会两张新牌必须能从冻结快照里拿到契约，且顺序是舞者在前。"""
    snapshot = builtin_registry.freeze()
    dancer = snapshot.require("wolf-killer-dancer")
    mask = snapshot.require("wolf-killer-mask")
    assert [c.contract_id for c in dancer.contracts] == ["dance_pool"]
    contracts = {
        contract.contract_id: contract
        for spec in snapshot.specs.values() for contract in spec.contracts
    }
    pool = contracts["dance_pool"]
    grant = contracts["mask_grant"]
    assert pool.multi_target_actions == {"pool": 3}
    assert pool.order < grant.order
    assert grant.selected_target_fact_namespaces == frozenset({"status_labels"})
    assert mask.initial_resources == dancer.initial_resources == {"poison_immune": 1}
    # Every preset must be acceptable to the frozen snapshot's own validation.
    for preset in STANDARD_PRESETS:
        snapshot.validate_role_counts(
            preset["role_counts"], sum(preset["role_counts"].values())
        )


def test_field_constraints_defaults():
    assert FIELD_CONSTRAINTS["min_players"] == 4
    assert FIELD_CONSTRAINTS["max_players"] == 12
    assert FIELD_CONSTRAINTS["min_werewolves"] == 1
    assert FIELD_CONSTRAINTS["min_good"] == 1


def test_every_preset_publishes_a_timeout_budget_matching_its_size():
    """A twelve-player field costs about 136 model calls against 100-111 for
    nine or ten, and that difference is what makes an hour run out."""
    for preset in STANDARD_PRESETS:
        players = sum(preset["role_counts"].values())
        expected = 7200 if players >= 12 else 3600
        assert preset["recommended_timeout_seconds"] == expected, preset["id"]


def test_schema_models_accept_catalog_shapes():
    item = RoleCatalogItem(**role_catalog(builtin_registry.freeze())[0])
    assert item.role_id
    assert item.rules
    preset = PresetItem(**STANDARD_PRESETS[0])
    assert preset.id
    constraints = ConstraintsResponse(**FIELD_CONSTRAINTS)
    assert constraints.min_players == 4


import pytest


@pytest.mark.asyncio
async def test_list_roles_endpoint_returns_every_registered_role():
    from app.api.routes import catalog_routes

    response = await catalog_routes.list_roles()

    assert len(response.roles) == 24
    assert response.roles[0].role_id


@pytest.mark.asyncio
async def test_list_presets_endpoint_returns_standards():
    from app.api.routes import catalog_routes

    response = await catalog_routes.list_presets()

    assert len(response.presets) == 15
    assert response.presets[0].name == "九人标准场"
    assert response.presets[0].recommended_timeout_seconds == 3600
    assert response.presets[-1].recommended_timeout_seconds == 7200
    # The wizard reads this key straight off the wire.
    assert "recommended_timeout_seconds" in response.model_dump()["presets"][0]


@pytest.mark.asyncio
async def test_get_constraints_endpoint_matches_constants():
    from app.api.routes import catalog_routes

    response = await catalog_routes.get_constraints()

    assert response.min_players == FIELD_CONSTRAINTS["min_players"]
    assert response.max_players == FIELD_CONSTRAINTS["max_players"]
