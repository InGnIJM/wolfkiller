from app.models.game import GameState, Camp
from app.models.actions import WinResult


class RuleEngine:
    """Checks win conditions for all camps."""

    def check_win(self, state: GameState) -> WinResult | None:
        alive_wolves = len(state.alive_by_camp(Camp.WEREWOLF))
        alive_seers = len([p for p in state.alive_players().values() if "seer" in p.role])
        alive_witches = len([p for p in state.alive_players().values() if "witch" in p.role])
        alive_hunters = len([p for p in state.alive_players().values() if "hunter" in p.role])
        alive_guards = len([p for p in state.alive_players().values() if "guard" in p.role])
        alive_idiots = len([p for p in state.alive_players().values() if "idiot" in p.role])
        alive_villagers = len([p for p in state.alive_players().values() if "villager" in p.role])
        alive_gods = alive_seers + alive_witches + alive_hunters + alive_guards + alive_idiots

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
