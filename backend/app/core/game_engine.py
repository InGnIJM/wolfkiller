from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import random
import uuid
from typing import Optional

from app.models.game import GameState, GamePhase, GameConfig, PlayerState
from app.models.actions import NightAction, VoteAction, SpeechRecord, DeathReport
from app.models.contracts import AcceptedAction, ActionContract, ActionRequest
from app.core.state_machine import GameStateMachine, GameEvent as SM_Event
from app.core.rule_engine import RuleEngine
from app.core.action_resolver import ActionResolver
from app.core.action_validator import ActionValidationError, ActionValidator
from app.core.event_bus import EventBus, GameEvent as BusEvent
from app.core.conversation_log import ConversationLog
from app.core.game_logger import GameLogger
from app.roles.registry import builtin_registry
from app.config import PipelineMode, pipeline_mode_from_env
from app.core.role_pipeline import PipelineObservation, PipelineResult, RolePipeline
from app.models.pipeline import SchedulePoint

logger = logging.getLogger(__name__)

VOTE_CONTRACT = ActionContract(
    contract_id="exile_vote",
    phase=GamePhase.VOTE_CASTING,
    action_types=("vote", "abstain"),
    actions_requiring_target=frozenset({"vote"}),
    resolution_priority=0,
    fallback_action_type="abstain",
)


class GameEngine:
    """Central orchestrator for a Werewolf game. All operations are encapsulated as methods."""

    def __init__(
        self,
        game_id: str | None = None,
        config: GameConfig | None = None,
        event_bus: EventBus | None = None,
        roles: dict[int, object] | None = None,
        memory_service: object | None = None,
        data_dir: str = "data",
        pipeline_mode: PipelineMode | None = None,
        pipeline_scheduler: object | None = None,
    ):
        if pipeline_mode is not None and type(pipeline_mode) is not PipelineMode:
            raise TypeError("pipeline_mode must be a PipelineMode")
        self.game_id = game_id or str(uuid.uuid4())[:8]
        self.config = config or GameConfig()
        self.sm = GameStateMachine()
        self.rule_engine = RuleEngine()
        self.action_resolver = ActionResolver()
        self.action_validator = ActionValidator()
        self.event_bus = event_bus or EventBus()
        self.roles = roles or {}
        self.memory_service = memory_service
        self.game_logger = GameLogger(data_dir=data_dir)
        self.conversation_log = ConversationLog(logger=self.game_logger, game_id=self.game_id)
        self.state = GameState(game_id=self.game_id, config=self.config)
        self._phase_delay: float = 2.0
        self._running = False
        self._paused = False
        self._last_words_given: set[tuple[int, int]] = set()
        self._accepted_action_results: dict[str, AcceptedAction] = {}
        self._pipeline_mode = pipeline_mode_from_env() if pipeline_mode is None else pipeline_mode
        self._pipeline_scheduler = pipeline_scheduler

    @property
    def pipeline_mode(self) -> PipelineMode:
        return self._pipeline_mode

    @staticmethod
    def _public_state_digest(state: GameState) -> str:
        document = json.dumps(
            state.get_public_state(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        )
        return hashlib.sha256(document.encode("utf-8")).hexdigest()

    async def run_schedule_point(self, point: SchedulePoint, legacy_runner) -> PipelineResult:
        if type(point) is not SchedulePoint: raise TypeError("point must be SchedulePoint")
        if not callable(legacy_runner): raise TypeError("legacy_runner must be callable")
        if self._pipeline_mode is not PipelineMode.V1 and self._pipeline_scheduler is None:
            raise ValueError("pipeline scheduler is required")
        loop = asyncio.get_running_loop()

        def v1_runner(state: GameState, ignored: SchedulePoint) -> PipelineObservation:
            before = set(state.accepted_action_keys)
            future = asyncio.run_coroutine_threadsafe(legacy_runner(), loop)
            future.result()
            accepted = tuple(sorted(state.accepted_action_keys - before))
            return PipelineObservation(
                accepted, (), self._public_state_digest(state), (),
            )

        runner = None if self._pipeline_mode is PipelineMode.V2 else v1_runner
        pipeline = RolePipeline(self._pipeline_mode, runner, self._pipeline_scheduler)
        result = await asyncio.to_thread(pipeline.run_point, self.state, point)
        if type(result) is not PipelineResult: raise TypeError("pipeline must return exact PipelineResult")
        return result

    @property
    def phase_delay(self) -> float:
        return self._phase_delay

    @phase_delay.setter
    def phase_delay(self, seconds: float) -> None:
        self._phase_delay = max(0.1, seconds)

    # =================================================================
    # Game Lifecycle
    # =================================================================

    async def start(self) -> None:
        self._running = True
        self.sm.reset()
        self.state = GameState(game_id=self.game_id, config=self.config)
        self.conversation_log = ConversationLog(logger=self.game_logger, game_id=self.game_id)
        self._accepted_action_results.clear()

        self.sm.transition(SM_Event.START)
        await self._broadcast_phase_change()

        self._assign_roles()
        self.sm.transition(SM_Event.ROLES_ASSIGNED)
        await self._broadcast_phase_change()

        await self._game_loop()

    async def stop(self) -> None:
        self._running = False

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    # =================================================================
    # Role Assignment
    # =================================================================

    def _camp_from_role(self, role_name: str) -> str:
        return "werewolf" if "werewolf" in role_name else "good"

    def _assign_roles(self) -> None:
        """Assign roles to player state based on the pre-built roles dict (already shuffled)."""
        players_dict: dict = {}
        for seat, role_instance in self.roles.items():
            role_name = role_instance.role_name
            camp = self._camp_from_role(role_name)
            player = PlayerState(seat_number=seat, role=role_name, camp=camp)
            if "witch" in role_name:
                player.has_antidote = True
                player.has_poison = True
            if "hunter" in role_name:
                player.has_gun = True
            self.state.players[seat] = player
            players_dict[str(seat)] = {
                "role": role_name,
                "camp": camp,
                "is_alive": True,
                "has_antidote": player.has_antidote,
                "has_poison": player.has_poison,
                "has_gun": player.has_gun,
            }
        self.game_logger.log_role_init(self.game_id, players_dict)

    # =================================================================
    # Main Loop
    # =================================================================

    async def _game_loop(self) -> None:
        while self._running and not self.sm.is_terminal():
            await self._wait_if_paused()
            phase = self.sm.get_state()

            if phase == GamePhase.NIGHT:
                await self._execute_night()
            elif phase == GamePhase.DAWN:
                await self._execute_dawn()
            elif phase == GamePhase.LAST_WORDS:
                await self._execute_last_words()
            elif phase == GamePhase.SPEECH:
                await self._execute_speech_round()
            elif phase == GamePhase.VOTE_CASTING:
                await self._execute_vote_casting()
            elif phase == GamePhase.VOTE_RESOLUTION:
                await self._execute_vote_resolution()
            elif phase == GamePhase.GAME_OVER:
                break

    async def _wait_if_paused(self) -> None:
        while self._paused and self._running:
            await asyncio.sleep(0.1)

    # =================================================================
    # Night Phase
    # =================================================================

    async def _execute_night(self) -> None:
        self.state.round_number += 1
        self.state.night_actions.clear()
        self.state.last_wolf_kill_target = None

        all_actions: list[NightAction] = []
        wolf_seats = self._get_role_seats("werewolf")
        round_num = self.state.round_number

        # ── 1. 狼人睁眼 → 投票刀人（投票中的理由即为内部交流） ────
        logger.info(f"Night {round_num}: Werewolf phase starting, wolf_seats={wolf_seats}")
        await self._broadcast_night_substep("werewolf_open", highlight_seats=wolf_seats)
        await self._sleep_night_step()

        # Werewolf kill vote (each wolf's reasoning is visible to subsequent wolves as chat)
        logger.info(f"Night {round_num}: Werewolf kill vote starting")
        wolf_actions, wolf_target = await self.werewolf_kill(wolf_seats)
        logger.info(f"Night {round_num}: Werewolf kill complete, target={wolf_target}")
        all_actions.extend(wolf_actions)
        self.state.last_wolf_kill_target = wolf_target

        await self._broadcast_night_substep(
            "werewolf_close", highlight_seats=[], wolf_kill_target=wolf_target,
        )
        await self._sleep_night_step()

        # Log
        wolf_vote_dicts = [a.to_dict() for a in wolf_actions]
        self.game_logger.log_werewolf_kill(
            self.game_id, round_num, wolf_seats, wolf_target, wolf_vote_dicts,
        )

        # ── 2. 女巫睁眼 → 单次决定解药、毒药或放弃 ────────────────
        logger.info(f"Night {round_num}: Witch phase starting")
        witch = self._find_player_by_role("witch")
        witch_role = self.roles.get(witch.seat_number) if witch else None
        witch_action = None

        if witch and witch.is_alive and (witch.has_antidote or witch.has_poison):
            await self._broadcast_night_substep("witch_open", highlight_seats=[witch.seat_number])
            await self._sleep_night_step()

            # Only a witch with antidote is told the current wolf target.
            if witch.has_antidote and wolf_target is not None:
                sys_msg = f"今晚狼人刀了 {wolf_target} 号玩家。"
            elif witch.has_antidote:
                sys_msg = "今晚狼人没有刀人。"
            else:
                sys_msg = "你已没有解药，本夜只能选择使用毒药或放弃行动。"
            self.conversation_log.add_night_intel(
                sys_msg, round_num, "night", visible_to=[witch.seat_number],
            )

            witch_action = await self.witch_action(witch.seat_number)
            if witch_action is not None:
                all_actions.append(witch_action)
            if witch_action and witch_action.action_type == "save":
                self.game_logger.log_witch_save(
                    self.game_id, round_num, witch.seat_number, wolf_target, True,
                )
                sys_msg = f"你使用了【解药】，救活了 {wolf_target} 号玩家。"
                self.conversation_log.add_night_intel(
                    sys_msg, round_num, "night", visible_to=[witch.seat_number],
                )
                await self._broadcast_night_substep(
                    "witch_action", highlight_seats=[witch.seat_number],
                    action_seat=witch.seat_number, action=witch_action.to_dict(),
                    wolf_kill_target=None,
                )
                await self._sleep_night_step()
            elif witch_action:
                self.game_logger.log_witch_save(
                    self.game_id, round_num, witch.seat_number, wolf_target, False,
                )

            if witch_action and witch_action.action_type == "poison":
                self.game_logger.log_witch_poison(
                    self.game_id, round_num, witch.seat_number,
                    witch_action.target_seat,
                )
                sys_msg = f"你使用了【毒药】，毒杀了 {witch_action.target_seat} 号玩家。"
                self.conversation_log.add_night_intel(
                    sys_msg, round_num, "night", visible_to=[witch.seat_number],
                )
                await self._broadcast_night_substep(
                    "witch_action", highlight_seats=[witch.seat_number],
                    action_seat=witch.seat_number,
                    action=witch_action.to_dict(),
                    wolf_kill_target=wolf_target,
                )
                await self._sleep_night_step()

            await self._broadcast_night_substep("witch_close", highlight_seats=[])
            await self._sleep_night_step()

        # ── 3. 预言家睁眼 → 查验身份 ──────────────────────────
        logger.info(f"Night {round_num}: Seer phase starting")
        seer = self._find_player_by_role("seer")
        if seer and seer.is_alive:
            await self._broadcast_night_substep("seer_open", highlight_seats=[seer.seat_number])
            await self._sleep_night_step()

            check_action = await self.seer_check(seer.seat_number)
            if check_action and check_action.target_seat:
                all_actions.append(check_action)
                result = self.resolve_seer_check(check_action)
                self.game_logger.log_seer_check(
                    self.game_id, round_num, seer.seat_number,
                    check_action.target_seat, result,
                )
                result_cn = "狼人" if result == "werewolf" else "好人"
                sys_msg = f"你查验了 {check_action.target_seat} 号玩家，他是【{result_cn}】。"
                self.conversation_log.add_night_intel(
                    sys_msg, round_num, "night", visible_to=[seer.seat_number],
                )
                await self._broadcast_night_substep(
                    "seer_check", highlight_seats=[seer.seat_number],
                    action_seat=seer.seat_number,
                    action={**check_action.to_dict(), "seer_result": result},
                )
                await self._sleep_night_step()

            await self._broadcast_night_substep("seer_close", highlight_seats=[])
            await self._sleep_night_step()

        # ── 4. 结算死亡 ──────────────────────────────────────
        logger.info(f"Night {round_num}: Resolving actions, total actions={len(all_actions)}")
        accepted_actions = self._accept_night_actions(all_actions)
        self.state.night_actions = [
            NightAction(
                player_seat=accepted.request.actor_seat,
                action_type=accepted.command.action_type,
                target_seat=accepted.command.target_seat,
                reasoning=accepted.command.reasoning,
            )
            for accepted in accepted_actions
        ]
        deaths = self.action_resolver.resolve(self.state, accepted_actions)

        # Hunter death check & shoot
        hunter_seat = self.action_resolver.has_hunter_died(self.state, deaths)
        if hunter_seat is not None:
            hunter_death = await self.hunter_shoot(
                hunter_seat, emit_death_event=False,
            )
            if hunter_death:
                deaths.append(hunter_death)

        for d in deaths:
            self.state.death_history.append(d)
            await self.event_bus.publish(
                BusEvent.PLAYER_DIED, game_id=self.game_id, death=d,
            )

        self.game_logger.log_deaths(
            self.game_id, round_num, [d.to_dict() for d in deaths],
        )

        if self.memory_service:
            self.memory_service.save_memories(self.state)

        if await self._check_game_over():
            await self._broadcast_phase_change()
            return

        self.sm.transition(SM_Event.NIGHT_ACTIONS_COMPLETE)
        await self._broadcast_phase_change()

    # =================================================================
    # Night Operation Functions
    # =================================================================

    async def _request_night_action(
        self, seat: int, contract_id: str, operation: str | None = None
    ) -> AcceptedAction:
        player = self.state.players[seat]
        contract = next(
            contract
            for contract in builtin_registry.require(player.role).contracts
            if contract.contract_id == contract_id and contract.phase == GamePhase.NIGHT
        )
        if contract_id == "witch_action":
            action_types = []
            if player.has_antidote and self.state.last_wolf_kill_target is not None:
                action_types.append("save")
            if player.has_poison:
                action_types.append("poison")
            action_types.append("pass")
            contract = ActionContract(
                contract_id=contract.contract_id,
                phase=contract.phase,
                action_types=tuple(action_types),
                actions_requiring_target=frozenset(action_types) - {"pass"},
                resolution_priority=contract.resolution_priority,
                fallback_action_type=contract.fallback_action_type,
            )
        key_suffix = (
            f":{operation}"
            if operation and contract_id != "witch_action"
            else ""
        )
        request = ActionRequest(
            actor_seat=seat,
            role_id=player.role,
            contract=contract,
            phase=GamePhase.NIGHT,
            round_id=self.state.round_number,
            idempotency_key=(
                f"{self.state.round_number}:{GamePhase.NIGHT.value}:{seat}:"
                f"{contract.contract_id}{key_suffix}"
            ),
        )
        if request.idempotency_key in self.state.accepted_action_keys:
            raise ActionValidationError("action already accepted")
        role = self.roles[seat]
        accepted = await role.request_action(
            self.state, self.conversation_log, request
        )
        self._accepted_action_results[request.idempotency_key] = accepted
        return accepted

    @staticmethod
    def _night_action_from_accepted(accepted: AcceptedAction) -> NightAction:
        return NightAction(
            player_seat=accepted.request.actor_seat,
            action_type=accepted.command.action_type,
            target_seat=accepted.command.target_seat,
            reasoning=accepted.command.reasoning,
        )

    async def _request_hunter_action(self, seat: int) -> AcceptedAction:
        player = self.state.players[seat]
        registered_contract = next(
            contract
            for contract in builtin_registry.require(player.role).contracts
            if contract.contract_id == "hunter_shoot"
        )
        contract = ActionContract(
            contract_id=registered_contract.contract_id,
            phase=self.state.phase,
            action_types=registered_contract.action_types,
            actions_requiring_target=registered_contract.actions_requiring_target,
            resolution_priority=registered_contract.resolution_priority,
            fallback_action_type=registered_contract.fallback_action_type,
        )
        request = ActionRequest(
            actor_seat=seat,
            role_id=player.role,
            contract=contract,
            phase=self.state.phase,
            round_id=self.state.round_number,
            idempotency_key=(
                f"{self.state.round_number}:{self.state.phase.value}:{seat}:"
                f"{contract.contract_id}"
            ),
        )
        return await self.roles[seat].request_action(
            self.state, self.conversation_log, request
        )

    def _accept_night_actions(
        self, actions: list[NightAction]
    ) -> list[AcceptedAction]:
        """Validate legacy night actions before the resolver settles them."""
        requests_by_actor: dict[int, list[ActionRequest]] = {}
        for request in builtin_registry.build_requests(
            self.state, self.roles, GamePhase.NIGHT,
        ):
            requests_by_actor.setdefault(request.actor_seat, []).append(request)
        accepted_actions: list[AcceptedAction] = []

        for action in actions:
            cached = next(
                (
                    accepted
                    for accepted in self._accepted_action_results.values()
                    if accepted.request.actor_seat == action.player_seat
                    and accepted.command.action_type == action.action_type
                    and accepted.command.target_seat == action.target_seat
                ),
                None,
            )
            if cached is not None:
                accepted_actions.append(cached)
                continue
            actor_requests = requests_by_actor.get(action.player_seat, [])
            if not actor_requests:
                logger.warning(
                    "Discarding night action without an issued contract (seat=%s)",
                    action.player_seat,
                )
                continue

            player = self.state.players.get(action.player_seat)
            if player is None:
                logger.warning(
                    "Discarding night action from missing player (seat=%s)",
                    action.player_seat,
                )
                continue
            night_contracts = [
                contract
                for contract in builtin_registry.require(player.role).contracts
                if contract.phase == GamePhase.NIGHT
            ]
            matching_contracts = [
                contract
                for contract in night_contracts
                if action.action_type in contract.action_types
            ]
            if len(matching_contracts) > 1:
                logger.warning(
                    "Discarding night action with ambiguous contract (seat=%s, action=%s)",
                    action.player_seat, action.action_type,
                )
                continue

            if matching_contracts:
                contract = matching_contracts[0]
                idempotency_key = (
                    f"{self.state.round_number}:{GamePhase.NIGHT.value}:"
                    f"{action.player_seat}:{contract.contract_id}"
                )
                if idempotency_key in self.state.accepted_action_keys:
                    logger.warning(
                        "Discarding duplicate night action for accepted contract "
                        "(seat=%s, contract=%s)",
                        action.player_seat, contract.contract_id,
                    )
                    continue
                matching_requests = [
                    request
                    for request in actor_requests
                    if request.contract.contract_id == contract.contract_id
                ]
                if len(matching_requests) != 1:
                    logger.warning(
                        "Discarding night action without an active matching contract "
                        "(seat=%s, action=%s)",
                        action.player_seat, action.action_type,
                    )
                    continue
                request = matching_requests[0]
            elif len(night_contracts) == 1:
                request = actor_requests[0]
            else:
                logger.warning(
                    "Discarding night action without a unique matching contract "
                    "(seat=%s, action=%s)",
                    action.player_seat, action.action_type,
                )
                continue

            cached = self._accepted_action_results.get(request.idempotency_key)
            if cached is not None:
                accepted_actions.append(cached)
                continue

            if request.idempotency_key in self.state.accepted_action_keys:
                logger.warning(
                    "Discarding duplicate night action for accepted contract "
                    "(seat=%s, contract=%s)",
                    action.player_seat, request.contract.contract_id,
                )
                continue

            payload = {
                "action_type": action.action_type,
                "target_seat": action.target_seat,
                "reasoning": action.reasoning,
            }
            try:
                accepted_actions.append(
                    self.action_validator.validate_and_accept(
                        self.state, request, payload,
                    )
                )
            except ActionValidationError as error:
                logger.warning(
                    "Night action rejected; using safe fallback (seat=%s): %s",
                    action.player_seat, error,
                )
                try:
                    accepted_actions.append(
                        self.action_validator.safe_fallback(self.state, request)
                    )
                except ActionValidationError as fallback_error:
                    logger.warning(
                        "Night action fallback rejected (seat=%s): %s",
                        action.player_seat, fallback_error,
                    )

        return accepted_actions

    async def werewolf_kill(self, wolf_seats: list[int]) -> tuple[list[NightAction], Optional[int]]:
        """Werewolves vote on kill target. Returns all actions and resolved target."""
        actions: list[NightAction] = []
        round_num = self.state.round_number

        for seat in wolf_seats:
            if seat not in self.roles:
                continue
            try:
                accepted = await self._request_night_action(seat, "werewolf_kill")
                action = self._night_action_from_accepted(accepted)
            except Exception as e:
                logger.error(f"Werewolf kill error (seat={seat}): {e}")
                action = None
            if action:
                actions.append(action)
                # Log each wolf's vote as werewolf chat — subsequent wolves see this to coordinate
                target_str = f"{action.target_seat}号" if action.target_seat else "弃权"
                reason = action.reasoning.strip() if action.reasoning else ""
                if not reason:
                    reason = "（未说明理由）" if action.target_seat else "（观望一轮，不急于行动）"
                self.conversation_log.add_werewolf_chat(
                    seat, "wolf-killer-werewolf",
                    f"我选择刀{target_str}。理由：{reason}",
                    round_num,
                )
                await self._broadcast_night_substep(
                    "werewolf_vote", highlight_seats=wolf_seats,
                    action_seat=seat, action=action.to_dict(),
                )
                await self._sleep_night_step(0.8)

        target = self.action_resolver._resolve_wolf_kill(actions)

        await self._broadcast_night_substep(
            "werewolf_target", highlight_seats=wolf_seats,
            wolf_kill_target=target,
        )
        await self._sleep_night_step()

        return actions, target

    async def witch_action(self, witch_seat: int) -> Optional[NightAction]:
        """Request the witch's single action for the current night."""
        player = self.state.players.get(witch_seat)
        if (
            witch_seat not in self.roles
            or player is None
            or not (player.has_antidote or player.has_poison)
        ):
            return None
        try:
            accepted = await self._request_night_action(witch_seat, "witch_action")
            return self._night_action_from_accepted(accepted)
        except Exception as error:
            logger.error("Witch action error (seat=%s): %s", witch_seat, error)
            return None

    async def witch_save(self, witch_seat: int, wolf_target: Optional[int]) -> bool:
        """Witch decides whether to use antidote. Returns True if used."""
        if witch_seat not in self.roles or wolf_target is None:
            return False
        player = self.state.players.get(witch_seat)
        if player is None or not player.has_antidote:
            return False
        try:
            accepted = await self._request_night_action(
                witch_seat, "witch_action", "save"
            )
            return (
                accepted.command.action_type == "save"
                and accepted.command.target_seat == wolf_target
            )
        except Exception as e:
            logger.error(f"Witch save error (seat={witch_seat}): {e}")
            return False

    async def witch_poison(self, witch_seat: int, wolf_target: Optional[int]) -> Optional[NightAction]:
        """Witch decides whether to use poison. Returns action or None."""
        if witch_seat not in self.roles:
            return None
        player = self.state.players.get(witch_seat)
        if player is None or not player.has_poison:
            return None
        try:
            accepted = await self._request_night_action(
                witch_seat, "witch_action", "poison"
            )
            return self._night_action_from_accepted(accepted)
        except Exception as e:
            logger.error(f"Witch poison error (seat={witch_seat}): {e}")
            return None

    async def seer_check(self, seer_seat: int) -> Optional[NightAction]:
        """Seer decides who to check. Returns the action."""
        if seer_seat not in self.roles:
            return None
        try:
            accepted = await self._request_night_action(seer_seat, "seer_check")
            return self._night_action_from_accepted(accepted)
        except Exception as e:
            logger.error(f"Seer check error (seat={seer_seat}): {e}")
            return None

    def resolve_seer_check(self, action: NightAction) -> str:
        """Return 'werewolf' or 'good' for a seer check target."""
        if action.target_seat is None:
            return "good"
        target = self.state.players.get(action.target_seat)
        if target is None:
            return "good"
        return "werewolf" if "werewolf" in target.role else "good"

    def get_night_deaths(self) -> list[DeathReport]:
        """Get all deaths from the current round."""
        return [d for d in self.state.death_history if d.round_number == self.state.round_number]

    async def hunter_shoot(
        self, hunter_seat: int, *, emit_death_event: bool = True,
    ) -> Optional[DeathReport]:
        """Hunter shoots a player on death. Returns DeathReport or None."""
        hunter = self.roles.get(hunter_seat)
        if hunter is None:
            return None
        player = self.state.players.get(hunter_seat)
        if player is None or not player.has_gun:
            return None

        self.game_logger.log_operation(
            self.game_id, "hunter_death", self.state.round_number, "night",
            seat=hunter_seat, data={"message": "猎人死亡，可以开枪"},
        )
        sys_msg = "你被杀害了！作为猎人，你可以开枪带走一名玩家。"
        self.conversation_log.add_night_intel(
            sys_msg, self.state.round_number, "night", visible_to=[hunter_seat],
        )

        try:
            accepted = await self._request_hunter_action(hunter_seat)
        except Exception as e:
            logger.error(f"Hunter shoot LLM error (seat={hunter_seat}): {e}")
            self.game_logger.log_hunter_shoot(
                self.game_id, self.state.round_number, hunter_seat, None,
            )
            return None

        if (
            accepted.command.action_type == "pass"
            or accepted.command.target_seat is None
        ):
            self.game_logger.log_hunter_shoot(
                self.game_id, self.state.round_number, hunter_seat, None,
            )
            return None

        death = self.action_resolver.resolve_hunter_shoot(
            self.state, hunter_seat, accepted
        )
        if death:
            if emit_death_event:
                await self.event_bus.publish(
                    BusEvent.PLAYER_DIED, game_id=self.game_id, death=death,
                )
            self.game_logger.log_hunter_shoot(
                self.game_id, self.state.round_number, hunter_seat,
                accepted.command.target_seat,
            )
            sys_msg = f"你开枪带走了 {accepted.command.target_seat} 号玩家。"
            self.conversation_log.add_night_intel(
                sys_msg, self.state.round_number, "night", visible_to=[hunter_seat],
            )
        else:
            # Target was dead or invalid, log pass (gun not consumed by resolver)
            self.game_logger.log_hunter_shoot(
                self.game_id, self.state.round_number, hunter_seat, None,
            )
        return death

    async def give_last_words(self, seat: int, cause: str, death_round: int) -> Optional[str]:
        """Generate last words for a dying player. Standalone function with validation.

        Eligibility:
        - First-night deaths (wolf_kill, poison) in round 1
        - Vote-exiled players (any round)

        Validation:
        - Player must exist and not have already given last words
        - Death cause must be eligible
        """
        night_causes = ("wolf_kill", "poison")
        is_first_night_death = cause in night_causes and death_round == 1
        is_exile = cause == "exile"
        if not (is_first_night_death or is_exile):
            return None

        death_key = (seat, death_round, cause)
        if death_key in self._last_words_given:
            return None

        player = self.state.players.get(seat)
        if player is None:
            return None

        self._last_words_given.add(death_key)

        speech_text = await self.speak(seat, "last_words")
        if speech_text:
            self.state.speeches.append(SpeechRecord(
                player_seat=seat, text=speech_text,
                round_number=self.state.round_number,
            ))
            self.conversation_log.add_public_speech(
                seat, player.role, speech_text,
                self.state.round_number, "last_words",
            )
            await self.event_bus.publish(
                BusEvent.SPEECH_MADE,
                game_id=self.game_id,
                speech=SpeechRecord(
                    player_seat=seat, text=speech_text,
                    round_number=self.state.round_number,
                ),
            )
            self.game_logger.log_speech(
                self.game_id, self.state.round_number, "last_words",
                seat, speech_text,
            )

        return speech_text

    # =================================================================
    # Dawn Phase
    # =================================================================

    async def _execute_dawn(self) -> None:
        await asyncio.sleep(self._phase_delay * 0.5)

        round_deaths = self.get_night_deaths()
        self.conversation_log.add_death_announcement(round_deaths, self.state.round_number)

        self.sm.transition(SM_Event.DAWN_COMPLETE)
        await self._broadcast_phase_change()

    # =================================================================
    # Last Words Phase
    # =================================================================

    async def _execute_last_words(self) -> None:
        # Only first-night deaths (wolf_kill, poison) get last words here.
        # Exiled players get last words immediately during vote_resolution.
        for death in self.state.death_history:
            await self.give_last_words(death.player_seat, death.cause, death.round_number)

        self.sm.transition(SM_Event.LAST_WORDS_COMPLETE)
        await self._broadcast_phase_change()

    # =================================================================
    # Speech Phase
    # =================================================================

    async def _execute_speech_round(self) -> None:
        alive = list(self.state.alive_players().items())
        if self.state.is_tiebreak:
            alive = [
                (seat, player)
                for seat, player in alive
                if seat not in self.state.supplemental_speakers
            ]
        self.state.speaking_order = [s for s, _ in alive]
        for seat, player in alive:
            self.state.current_speaker = seat
            speech_text = await self.speak(seat, "day_speech")
            if speech_text:
                self.state.speeches.append(SpeechRecord(
                    player_seat=seat, text=speech_text,
                    round_number=self.state.round_number,
                ))
                self.conversation_log.add_public_speech(
                    seat, player.role, speech_text,
                    self.state.round_number, "speech",
                )
                await self.event_bus.publish(
                    BusEvent.SPEECH_MADE,
                    game_id=self.game_id,
                    speech=SpeechRecord(
                        player_seat=seat, text=speech_text,
                        round_number=self.state.round_number,
                    ),
                )
                self.game_logger.log_speech(
                    self.game_id, self.state.round_number, "speech", seat, speech_text,
                )
                if self.state.is_tiebreak:
                    self.state.supplemental_speakers.add(seat)
            else:
                logger.warning(
                    f"Seat {seat}: speak() returned None/empty in speech round "
                    f"(alive={player.is_alive}, phase={self.state.phase.value}). "
                    f"This should not happen for a living player in speech phase."
                )

        self.state.current_speaker = None
        self.state.speaking_order = []
        self.sm.transition(SM_Event.SPEECHES_COMPLETE)
        await self._broadcast_phase_change()

    # =================================================================
    # Vote Casting Phase
    # =================================================================

    async def _execute_vote_casting(self) -> None:
        if not self.state.voted_seats:
            self.state.votes.clear()
        for seat in self.state.alive_players():
            if seat in self.state.voted_seats:
                continue
            vote = await self.vote(seat)
            if vote:
                self.state.votes.append(vote)
                self.state.voted_seats.add(seat)
                self.game_logger.log_vote(
                    self.game_id, self.state.round_number, seat, vote.target_seat,
                )
                await self.event_bus.publish(
                    BusEvent.VOTE_CAST, game_id=self.game_id, vote=vote,
                )

        self.sm.transition(SM_Event.VOTES_COMPLETE)
        await self._broadcast_phase_change()

    # =================================================================
    # Vote Resolution Phase
    # =================================================================

    async def _execute_tiebreak(self, candidates: list[int]) -> None:
        """Run exactly one persisted supplemental-speech and re-vote round."""
        if not self.state.is_tiebreak:
            self.conversation_log.add_vote_result(
                self.state.votes, None, self.state.round_number,
            )
            self.state.vote_round = 2
            self.state.is_tiebreak = True
            self.state.tiebreak_candidates = set(candidates)
            self.state.supplemental_speakers.clear()
            self.state.voted_seats.clear()

            self.conversation_log.add_system_message(
                "平票，进入补充发言轮次后重新投票。",
                self.state.round_number,
                "public",
            )

        alive_seats = set(self.state.alive_players())
        if alive_seats - self.state.supplemental_speakers:
            self.sm.set_state(GamePhase.SPEECH)
            await self._broadcast_phase_change()
            await self._execute_speech_round()

        if alive_seats - self.state.voted_seats:
            self.sm.set_state(GamePhase.VOTE_CASTING)
            await self._broadcast_phase_change()
            await self._execute_vote_casting()
        else:
            self.sm.set_state(GamePhase.VOTE_RESOLUTION)

        exiled_seat = self.resolve_votes()
        if exiled_seat is not None:
            player = self.state.players.get(exiled_seat)
            if player:
                player.mark_dead("exile")
                self.state.death_history.append(DeathReport(
                    player_seat=exiled_seat, cause="exile",
                    round_number=self.state.round_number,
                ))
                if "hunter" in player.role and player.has_gun:
                    hunter_death = await self.hunter_shoot(exiled_seat)
                    if hunter_death:
                        self.state.death_history.append(hunter_death)
                await self.give_last_words(
                    exiled_seat, "exile", self.state.round_number,
                )

        self.conversation_log.add_vote_result(
            self.state.votes, exiled_seat, self.state.round_number,
        )
        self._clear_tiebreak_state()
        if not await self._check_game_over():
            self.sm.transition(SM_Event.VOTE_RESOLVED)
        await self._broadcast_phase_change()

    async def _execute_vote_resolution(self) -> None:
        if self.state.is_tiebreak:
            await self._execute_tiebreak(list(self.state.tiebreak_candidates))
            return

        tally = self._tally_votes()
        tied_candidates = self._tied_top_candidates(tally)
        if tied_candidates:
            await self._execute_tiebreak(tied_candidates)
            return

        if not tally:
            self.conversation_log.add_vote_result(
                self.state.votes, None, self.state.round_number,
            )
            self._clear_tiebreak_state()
            if not await self._check_game_over():
                self.sm.transition(SM_Event.VOTE_RESOLVED)
            await self._broadcast_phase_change()
            return

        exiled_seat = self.resolve_votes()

        if exiled_seat is not None:
            player = self.state.players.get(exiled_seat)
            if player:
                player.mark_dead("exile")
                self.state.death_history.append(DeathReport(
                    player_seat=exiled_seat, cause="exile",
                    round_number=self.state.round_number,
                ))
                # Hunter shot FIRST (before last words, so the hunter can reference
                # their shooting decision in their final speech)
                if "hunter" in player.role and player.has_gun:
                    hunter_death = await self.hunter_shoot(exiled_seat)
                    if hunter_death:
                        self.state.death_history.append(hunter_death)
                # Exiled player gives last words AFTER shooting
                await self.give_last_words(exiled_seat, "exile", self.state.round_number)

        # Announce vote result
        if exiled_seat is not None:
            self.conversation_log.add_vote_result(
                self.state.votes, exiled_seat, self.state.round_number,
            )
        else:
            tie_final = "补充投票仍为平票，无人被放逐。"
            self.conversation_log.add_system_message(tie_final, self.state.round_number, "public")
            self.conversation_log.add_vote_result(
                self.state.votes, None, self.state.round_number,
            )

        if not await self._check_game_over():
            self.sm.transition(SM_Event.VOTE_RESOLVED)

        await self._broadcast_phase_change()

    # =================================================================
    # Day Operation Functions
    # =================================================================

    async def speak(self, seat: int, context: str) -> Optional[str]:
        """Generate speech for a player. Returns the speech text or None.

        Guarantees a non-empty string is returned for any living player in a
        valid speech context — if the role fails to generate speech for any
        reason, an emergency fallback is produced inline so the player is
        never silently skipped. Only returns None when the player truly
        cannot speak (no role found, dead, wrong phase).
        """
        role = self.roles.get(seat)
        if role is None:
            logger.warning(f"No role found for seat {seat} during speak() — skipping")
            return None
        try:
            result = await role.speak(self.state, self.conversation_log, context)
        except Exception as e:
            logger.error(f"Speech error (seat={seat}, context={context}): {e}")
            result = None

        if not result:
            logger.warning(
                f"Seat {seat}: role.speak() returned empty/None for context={context}. "
                f"Generating emergency fallback speech to prevent silent skip."
            )
            result = self._emergency_speech(seat, context)
        return result

    def _emergency_speech(self, seat: int, context: str) -> str:
        """Last-resort speech when the role's LLM completely fails.
        Ensures the player is never silently dropped from the conversation.
        """
        player = self.state.players.get(seat)
        role_cn = "玩家"
        if player and hasattr(player, "role"):
            role_map = {
                "wolf-killer-werewolf": "狼人",
                "wolf-killer-villager": "平民",
                "wolf-killer-seer": "预言家",
                "wolf-killer-witch": "女巫",
                "wolf-killer-hunter": "猎人",
            }
            role_cn = role_map.get(player.role, "玩家")

        if context == "last_words":
            return (
                f"我是{seat}号{role_cn}，我已经出局了。"
                f"希望好人能仔细分析场上局势，找出狼人。"
            )

        # Day speech: reference another alive player as plausible content
        alive = [s for s in self.state.alive_players() if s != seat]
        if alive:
            import random
            suspect = random.choice(alive)
            return (
                f"我是{seat}号，我目前比较关注{suspect}号玩家的发言。"
                f"前面几位的发言我都认真听了，"
                f"我会结合所有信息在投票时做出判断。"
            )
        return (
            f"我是{seat}号，现在场上人数很少了，"
            f"我需要仔细分析之前的发言，慎重做出今天的决定。"
        )

    async def vote(self, seat: int) -> Optional[VoteAction]:
        """Player casts a vote. Returns VoteAction or None."""
        role = self.roles.get(seat)
        player = self.state.players.get(seat)
        if role is None or player is None:
            return None
        request = ActionRequest(
            actor_seat=seat,
            role_id=player.role,
            contract=VOTE_CONTRACT,
            phase=GamePhase.VOTE_CASTING,
            round_id=self.state.round_number,
            idempotency_key=(
                f"{self.state.round_number}:{GamePhase.VOTE_CASTING.value}:"
                f"{self.state.vote_round}:{seat}:{VOTE_CONTRACT.contract_id}"
            ),
        )
        try:
            accepted = await role.request_action(
                self.state, self.conversation_log, request
            )
            return VoteAction(
                voter_seat=seat,
                target_seat=accepted.command.target_seat,
                reasoning=accepted.command.reasoning,
            )
        except Exception as e:
            logger.error(f"Vote error (seat={seat}): {e}")
            return None

    async def _check_game_over(self) -> bool:
        """Check win conditions. If game is over, handle cleanup and broadcast. Returns True if over."""
        win_result = self.rule_engine.check_win(self.state)
        if win_result:
            self.state.win_result = win_result.to_dict()
            self.state.phase = GamePhase.GAME_OVER
            self.sm.set_state(GamePhase.GAME_OVER)
            self.game_logger.log_game_over(
                self.game_id, self.state.round_number,
                win_result.winning_camp, win_result.reason,
            )
            await self.event_bus.publish(
                BusEvent.GAME_OVER, game_id=self.game_id, win_result=win_result,
            )
            return True
        return False

    def resolve_votes(self) -> Optional[int]:
        """Tally votes. Returns exiled seat, or None on tie."""
        tally = self._tally_votes()

        if not tally:
            return None

        max_votes = max(tally.values())
        top = [s for s, c in tally.items() if c == max_votes]

        self.game_logger.log_vote_result(
            self.game_id, self.state.round_number,
            top[0] if len(top) == 1 else None,
            tally,
        )

        return top[0] if len(top) == 1 else None

    def _tally_votes(self) -> dict[int, int]:
        tally: dict[int, int] = {}
        for vote in self.state.votes:
            if vote.target_seat is not None:
                tally[vote.target_seat] = tally.get(vote.target_seat, 0) + 1
        return tally

    @staticmethod
    def _tied_top_candidates(tally: dict[int, int]) -> list[int]:
        if not tally:
            return []
        max_votes = max(tally.values())
        top = [seat for seat, count in tally.items() if count == max_votes]
        return top if len(top) >= 2 else []

    def _clear_tiebreak_state(self) -> None:
        self.state.vote_round = 1
        self.state.is_tiebreak = False
        self.state.tiebreak_candidates.clear()
        self.state.supplemental_speakers.clear()
        self.state.voted_seats.clear()

    # =================================================================
    # Helpers
    # =================================================================

    def _get_role_seats(self, role_keyword: str) -> list[int]:
        return [
            s for s, p in self.state.players.items()
            if role_keyword in p.role and p.is_alive
        ]

    def _find_player_by_role(self, role_keyword: str) -> Optional[PlayerState]:
        for p in self.state.players.values():
            if role_keyword in p.role and p.is_alive:
                return p
        return None

    async def _broadcast_night_substep(self, step: str, **kwargs) -> None:
        await self.event_bus.publish(
            BusEvent.NIGHT_SUBSTEP, game_id=self.game_id, step=step,
            round_number=self.state.round_number, **kwargs,
        )

    async def _sleep_night_step(self, duration: float | None = None) -> None:
        if duration is None:
            duration = min(self._phase_delay * 0.3, 1.5)
        await asyncio.sleep(duration)

    async def _broadcast_phase_change(self) -> None:
        self.state.phase = self.sm.get_state()
        self.game_logger.log_phase_change(
            self.game_id, self.state.phase.value, self.state.round_number,
        )
        await self.event_bus.publish(
            BusEvent.PHASE_CHANGED, game_id=self.game_id, phase=self.state.phase.value,
            round_number=self.state.round_number, state=self.state,
        )
