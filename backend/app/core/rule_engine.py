from app.models.game import DEFAULT_VICTORY, GameState
from app.models.actions import WinResult
from app.core.win_attribution import GOOD, THIRD, WOLF, seat_allegiances, winner_seats


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

    Headcount uses allegiance, which equals the stored camp until a *link*
    moves it into the third party. A crush never moves it: an Admirer counts as
    a villager and only shares the crush's victory (``winner_seats``). A living
    third-party allegiance blocks every other ending.
    """

    def check_win(self, state: GameState) -> WinResult | None:
        allegiances = seat_allegiances(state)
        alive = [seat for seat, player in state.players.items() if player.is_alive]
        alive_sides = [allegiances[seat] for seat in alive]

        # A living third party has to be the last side standing, or nobody has
        # won yet. This is checked before 屠边 and 屠城 alike.
        if THIRD in alive_sides:
            if alive and all(side == THIRD for side in alive_sides):
                return self._result(state, THIRD, "all_others_dead")
            return None

        if getattr(state.config, "victory", DEFAULT_VICTORY) == "city":
            # 狼刀在先: an empty town is a wolf win, same bias as 屠边's empty board.
            if GOOD not in alive_sides:
                return self._result(state, WOLF, "all_good_dead")
            if WOLF not in alive_sides:
                return self._result(state, GOOD, "all_wolves_dead")
            return None

        alive_players = state.alive_players().values()
        alive_gods = len([player for player in alive_players if "god" in _role_tags(player.role)])
        alive_villagers = len([
            player for player in alive_players if "villager" in _role_tags(player.role)
        ])
        alive_wolves = sum(1 for seat in alive if allegiances[seat] == WOLF)

        # 狼刀在先: check wolf win conditions first
        if alive_gods == 0:
            return self._result(state, WOLF, "all_gods_dead")

        if alive_villagers == 0:
            return self._result(state, WOLF, "all_villagers_dead")

        alive_good = len(alive) - alive_wolves
        if alive_wolves > alive_good:
            return self._result(state, WOLF, "wolves_outnumber_good")

        if alive_wolves == 0:
            return self._result(state, GOOD, "all_wolves_dead")

        return None

    @staticmethod
    def _result(state: GameState, camp: str, reason: str) -> WinResult:
        """The camp verdict plus whoever shares it (``None`` on every usual board)."""
        return WinResult(
            winning_camp=camp, reason=reason,
            winners=winner_seats(state, camp),
        )

    def is_game_over(self, state: GameState) -> bool:
        return self.check_win(state) is not None
