import random
from typing import Optional
from app.models.game import GameState
from app.models.actions import NightAction, DeathReport


class ActionResolver:
    """Resolves all night actions: wolf kill → witch save/poison → seer check → hunter check."""

    def resolve(self, state: GameState, actions: list[NightAction]) -> list[DeathReport]:
        """Resolve night actions and return deaths. Does NOT handle hunter shoot —
        caller must check for hunter death and prompt the hunter separately."""
        wolf_actions = [a for a in actions if a.action_type == "kill"]
        witch_actions = [a for a in actions if a.action_type in ("save", "poison")]
        seer_actions = [a for a in actions if a.action_type == "check"]

        # 1. Resolve wolf kill target (majority vote)
        wolf_target = self._resolve_wolf_kill(wolf_actions)
        state.last_wolf_kill_target = wolf_target

        # 2. Process witch actions (one potion per night enforced by engine)
        saved = self._process_witch_save(state, witch_actions)
        poisoned_target = self._process_witch_poison(state, witch_actions)

        # 3. Process seer checks
        self._process_seer_checks(state, seer_actions)

        # 4. Mark deaths
        deaths: list[DeathReport] = []

        if wolf_target is not None and not saved:
            player = state.players.get(wolf_target)
            if player and player.is_alive:
                player.mark_dead("wolf_kill")
                deaths.append(DeathReport(
                    player_seat=wolf_target, cause="wolf_kill",
                    round_number=state.round_number,
                ))

        if poisoned_target is not None:
            player = state.players.get(poisoned_target)
            if player and player.is_alive:
                player.mark_dead("poison")
                deaths.append(DeathReport(
                    player_seat=poisoned_target, cause="poison",
                    round_number=state.round_number,
                ))

        return deaths

    def has_hunter_died(self, state: GameState, deaths: list[DeathReport]) -> Optional[int]:
        """Check if a hunter with a gun died (not from poison).
        Returns the hunter's seat number, or None."""
        for death in deaths:
            if death.cause == "poison":
                continue
            player = state.players.get(death.player_seat)
            if player and "hunter" in player.role and player.has_gun:
                return death.player_seat
        return None

    def resolve_hunter_shoot(
        self, state: GameState, hunter_seat: int, action: NightAction
    ) -> Optional[DeathReport]:
        """Resolve hunter's shot. Returns DeathReport on success, None if invalid.
        Does NOT consume the gun if the target is invalid (dead or missing)."""
        hunter = state.players.get(hunter_seat)
        if not hunter or not hunter.has_gun:
            return None

        target_seat = action.target_seat
        if target_seat is None:
            return None

        target = state.players.get(target_seat)
        if not target or not target.is_alive:
            return None  # don't consume gun for invalid target

        hunter.has_gun = False
        target.mark_dead("hunter_shot")
        return DeathReport(
            player_seat=target_seat, cause="hunter_shot",
            round_number=state.round_number,
        )

    # ── Private helpers ───────────────────────────────────────────

    def _resolve_wolf_kill(self, actions: list[NightAction]) -> Optional[int]:
        if not actions:
            return None
        targets = [a.target_seat for a in actions if a.target_seat is not None]
        if not targets:
            return None

        vote_counts: dict[int, int] = {}
        for t in targets:
            vote_counts[t] = vote_counts.get(t, 0) + 1

        max_votes = max(vote_counts.values())
        top = [t for t, c in vote_counts.items() if c == max_votes]
        return random.choice(top)

    def _process_witch_save(
        self, state: GameState, actions: list[NightAction]
    ) -> bool:
        for action in actions:
            if action.action_type != "save":
                continue
            witch = state.players.get(action.player_seat)
            if witch and witch.has_antidote:
                witch.has_antidote = False
                return True
        return False

    def _process_witch_poison(
        self, state: GameState, actions: list[NightAction]
    ) -> Optional[int]:
        for action in actions:
            if action.action_type != "poison":
                continue
            witch = state.players.get(action.player_seat)
            if witch and witch.has_poison:
                target_seat = action.target_seat
                if target_seat is not None:
                    target = state.players.get(target_seat)
                    if target and not target.is_alive:
                        return None  # target already dead, don't consume poison
                witch.has_poison = False
                return target_seat
        return None

    def _process_seer_checks(
        self, state: GameState, actions: list[NightAction]
    ) -> None:
        for action in actions:
            if action.action_type != "check":
                continue
            if action.target_seat is None:
                continue
            target = state.players.get(action.target_seat)
            if target is None or not target.is_alive:
                continue
            result = "werewolf" if "werewolf" in target.role else "good"
            seer = state.players.get(action.player_seat)
            if seer:
                seer.check_results.append({
                    "target_seat": action.target_seat,
                    "result": result,
                    "round": state.round_number,
                })
