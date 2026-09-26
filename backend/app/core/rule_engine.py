from app.models.game import GameState, Camp
from app.models.actions import WinResult


def _role_tags(role_id: str) -> frozenset[str]:
    """Tags a role declares in its spec; unknown roles carry no tag."""
    from app.roles.registry import builtin_registry

    try:
        spec = builtin_registry.require(role_id)
    except ValueError:
        return frozenset()
    pipeline = builtin_registry.pipeline_spec(role_id)
    return pipeline.tags if pipeline is not None else spec.tags


class RuleEngine:
    """Checks win conditions for all camps.

    God and plain-villager counts come from the ``god`` / ``villager`` tags each
    role declares in its spec, never from its id: an id is free-form, so
    matching on it silently drops any role whose name does not happen to contain
    a magic substring, and a dropped god can trigger an early wolf win.
    """

    def check_win(self, state: GameState) -> WinResult | None:
        alive_wolves = len(state.alive_by_camp(Camp.WEREWOLF))
        alive = state.alive_players().values()
        alive_gods = len([p for p in alive if "god" in _role_tags(p.role)])
        alive_villagers = len([p for p in alive if "villager" in _role_tags(p.role)])

        # 狼刀在先: check wolf win conditions first
        if alive_gods == 0:
            return WinResult(winning_camp=Camp.WEREWOLF.value, reason="all_gods_dead")

        if alive_villagers == 0:
            return WinResult(winning_camp=Camp.WEREWOLF.value, reason="all_villagers_dead")

        alive_good = len(state.alive_players()) - alive_wolves
        if alive_wolves > alive_good:
            return WinResult(
                winning_camp=Camp.WEREWOLF.value,
                reason="wolves_outnumber_good",
            )

        if alive_wolves == 0:
            return WinResult(winning_camp=Camp.GOOD.value, reason="all_wolves_dead")

        return None

    def is_game_over(self, state: GameState) -> bool:
        return self.check_win(state) is not None
