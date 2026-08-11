from typing import Optional
from app.models.contracts import AcceptedAction
from app.models.game import GameState
from app.models.actions import NightAction, DeathReport


class ActionResolver:
    """Resolves all night actions: wolf kill → witch save/poison → seer check → hunter check."""

    def resolve(
        self, state: GameState, actions: list[AcceptedAction | NightAction]
    ) -> list[DeathReport]:
        """Resolve night actions and return deaths. Does NOT handle hunter shoot —
        caller must check for hunter death and prompt the hunter separately."""
        resolved_actions = [self._as_night_action(action) for action in actions]
        legacy_action_ids = {
            id(resolved)
            for original, resolved in zip(actions, resolved_actions)
            if isinstance(original, NightAction)
        }
        wolf_actions = [a for a in resolved_actions if a.action_type == "kill"]
        witch_actions = [a for a in resolved_actions if a.action_type in ("save", "poison")]
        seer_actions = [a for a in resolved_actions if a.action_type == "check"]

        # 1. Resolve wolf kill target (majority vote)
        wolf_target = self._resolve_wolf_kill(wolf_actions)
        state.last_wolf_kill_target = wolf_target

        # 2. Process witch actions (one potion per night enforced by engine)
        saved = self._process_witch_save(
            state, wolf_target, witch_actions, legacy_action_ids
        )
        poisoned_target = self._process_witch_poison(
            state, witch_actions, legacy_action_ids
        )

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

    def _as_night_action(self, action: AcceptedAction | NightAction) -> NightAction:
        if isinstance(action, AcceptedAction):
            return NightAction(
                player_seat=action.request.actor_seat,
                action_type=action.command.action_type,
                target_seat=action.command.target_seat,
                reasoning=action.command.reasoning,
            )
        if isinstance(action, NightAction):
            # NightAction is a legacy, engine-internal trusted command. External
            # model output must first become AcceptedAction through ActionValidator.
            return action
        raise TypeError("resolver requires AcceptedAction or trusted NightAction")

    def _resolve_wolf_kill(self, actions: list[NightAction]) -> Optional[int]:
        if not actions:
            return None
        targets = [
            a.target_seat
            for a in actions
            if isinstance(a.target_seat, int) and not isinstance(a.target_seat, bool)
            and a.target_seat > 0
        ]
        if not targets:
            return None

        vote_counts: dict[int, int] = {}
        for t in targets:
            vote_counts[t] = vote_counts.get(t, 0) + 1

        max_votes = max(vote_counts.values())
        top = [t for t, c in vote_counts.items() if c == max_votes]
        return min(top)

    def _process_witch_save(
        self,
        state: GameState,
        wolf_target: Optional[int],
        actions: list[NightAction],
        legacy_action_ids: set[int],
    ) -> bool:
        for action in actions:
            if action.action_type != "save":
                continue
            is_legacy = id(action) in legacy_action_ids
            if is_legacy and action.target_seat is None:
                witch = state.players.get(action.player_seat)
                if witch and witch.has_antidote:
                    witch.has_antidote = False
                    return True
            elif action.target_seat == wolf_target:
                if is_legacy:
                    witch = state.players.get(action.player_seat)
                    if not witch or not witch.has_antidote:
                        continue
                    witch.has_antidote = False
                return True
        return False

    def _process_witch_poison(
        self,
        state: GameState,
        actions: list[NightAction],
        legacy_action_ids: set[int],
    ) -> Optional[int]:
        for action in actions:
            if action.action_type != "poison":
                continue
            if action.target_seat is None or action.target_seat == 0:
                return None
            if id(action) in legacy_action_ids:
                witch = state.players.get(action.player_seat)
                if not witch or not witch.has_poison:
                    continue
                target = state.players.get(action.target_seat)
                if target and not target.is_alive:
                    return None
                witch.has_poison = False
            return action.target_seat
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
