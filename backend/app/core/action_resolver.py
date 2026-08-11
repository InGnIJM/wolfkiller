from typing import Optional
from app.core.action_validator import ActionValidationError
from app.models.contracts import AcceptedAction
from app.models.game import GameState
from app.models.actions import NightAction, DeathReport


class ActionResolver:
    """Resolves all night actions: wolf kill → witch save/poison → seer check → hunter check."""

    def resolve(
        self, state: GameState, actions: list[AcceptedAction]
    ) -> list[DeathReport]:
        """Resolve night actions and return deaths. Does NOT handle hunter shoot —
        caller must check for hunter death and prompt the hunter separately."""
        self._validate_witch_actions(state, actions)
        resolved_actions = [self._as_night_action(action) for action in actions]
        wolf_actions = [a for a in resolved_actions if a.action_type == "kill"]
        witch_actions = [a for a in resolved_actions if a.action_type in ("save", "poison")]
        seer_actions = [a for a in resolved_actions if a.action_type == "check"]

        # 1. Resolve wolf kill target (majority vote)
        wolf_target = self._resolve_wolf_kill(wolf_actions)
        state.last_wolf_kill_target = wolf_target

        # 2. Process witch actions (one potion per night enforced by engine)
        saved = self._process_witch_save(wolf_target, witch_actions)
        poisoned_target = self._process_witch_poison(witch_actions)

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
        self, state: GameState, hunter_seat: int, action: AcceptedAction
    ) -> Optional[DeathReport]:
        """Resolve hunter's shot. Returns DeathReport on success, None if invalid.
        Does NOT consume the gun if the target is invalid (dead or missing)."""
        if not isinstance(action, AcceptedAction):
            raise TypeError("resolver requires AcceptedAction")
        hunter = state.players.get(hunter_seat)
        if not hunter or not hunter.has_gun:
            return None

        if action.request.actor_seat != hunter_seat:
            return None
        if action.command.action_type != "shoot":
            return None
        target_seat = action.command.target_seat
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

    def _validate_witch_actions(
        self, state: GameState, actions: list[AcceptedAction]
    ) -> None:
        """Reject multiple actions from one witch in the same round."""
        witch_action_keys: set[tuple[int, int]] = set()
        for action in actions:
            if not isinstance(action, AcceptedAction):
                continue
            actor = state.players.get(action.request.actor_seat)
            if (
                action.request.contract.contract_id != "witch_action"
                or action.request.role_id != "wolf-killer-witch"
                or actor is None
                or actor.role != "wolf-killer-witch"
            ):
                continue
            if action.command.action_type not in {"save", "poison", "pass"}:
                continue
            key = (action.request.actor_seat, action.request.round_id)
            if key in witch_action_keys:
                raise ActionValidationError(
                    "multiple witch actions for one actor in the same round"
                )
            witch_action_keys.add(key)

    def _as_night_action(self, action: AcceptedAction) -> NightAction:
        if isinstance(action, AcceptedAction):
            return NightAction(
                player_seat=action.request.actor_seat,
                action_type=action.command.action_type,
                target_seat=action.command.target_seat,
                reasoning=action.command.reasoning,
            )
        raise TypeError("resolver requires AcceptedAction")

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
        wolf_target: Optional[int],
        actions: list[NightAction],
    ) -> bool:
        for action in actions:
            if action.action_type != "save":
                continue
            if action.target_seat is not None and action.target_seat == wolf_target:
                return True
        return False

    def _process_witch_poison(
        self,
        actions: list[NightAction],
    ) -> Optional[int]:
        for action in actions:
            if action.action_type != "poison":
                continue
            if action.target_seat is None or action.target_seat == 0:
                return None
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
