import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import AIMessage

from app.agents.prompt_builder import PromptBuilder
from app.core.action_validator import ActionValidationError
from app.core.conversation_log import ConversationLog
from app.core.game_engine import VOTE_CONTRACT
from app.models.actions import VoteAction
from app.models.contracts import ActionCommand, ActionRequest
from app.models.game import GameState, GamePhase, PlayerState
from app.roles.base import BaseRole
from app.roles.registry import builtin_registry


class ModelStub:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages = []

    async def ainvoke(self, messages):
        self.messages.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ClientStub:
    def __init__(self, plain=(), tools=(), strict=()):
        self.plain_model = ModelStub(plain)
        self.tools_model = ModelStub(tools)
        self.strict_model = ModelStub(strict)

    def get_model(self):
        return self.plain_model

    def get_model_with_tools(self, tools):
        return self.tools_model

    def get_model_with_action_tool(self, contract):
        return self.strict_model


def tool_msg(name: str, text: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"text": text}, "id": "call_1"}],
    )


def make_state(phase=GamePhase.SPEECH, seats=2) -> GameState:
    state = GameState(game_id="role-test", phase=phase, round_number=1)
    specs = builtin_registry.freeze().specs
    state.players = {
        seat: PlayerState(seat, "wolf-killer-villager", specs["wolf-killer-villager"].camp_id)
        for seat in range(1, seats + 1)
    }
    return state


def make_role(seat=1, client=None, state=None):
    return BaseRole(seat, "wolf-killer-villager", PromptBuilder(), client or ClientStub())


class TestBaseRoleSpeech:
    @pytest.mark.asyncio
    async def test_speak_uses_tool_calling_and_validates_text(self):
        client = ClientStub(tools=[tool_msg("speak", "我认为三号玩家的发言非常可疑，值得重点关注。")])
        role = make_role(client=client)
        state = make_state()

        text = await role.speak(state, ConversationLog(), "day_speech")

        assert text.startswith("我认为三号玩家")
        assert len(client.tools_model.messages) == 1

    @pytest.mark.asyncio
    async def test_speak_retries_once_when_no_tool_call(self):
        client = ClientStub(tools=[
            AIMessage(content="普通文本，没有调用函数。"),
            tool_msg("speak", "我认为三号玩家的发言非常可疑，值得重点关注。"),
        ])
        role = make_role(client=client)
        state = make_state()

        text = await role.speak(state, ConversationLog(), "day_speech")

        assert text.startswith("我认为三号玩家")
        assert len(client.tools_model.messages) == 2
        assert "你刚才没有调用发言函数" in client.tools_model.messages[1][1].content

    @pytest.mark.asyncio
    async def test_speak_falls_back_after_both_attempts_fail(self):
        client = ClientStub(tools=[AIMessage(content="没有工具调用。"), AIMessage(content="还是没有。")])
        role = make_role(client=client)
        state = make_state()

        text = await role.speak(state, ConversationLog(), "day_speech")

        assert text is not None and len(text) >= 15
        assert "1号" in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("response,expected_fallback", [
        (tool_msg("last_words", "我是狼人我要走了"), True),   # wrong fn for day_speech
        (tool_msg("speak", ""), True),                       # empty text
        (tool_msg("speak", "太短了"), True),                  # below 15 chars
    ])
    async def test_speak_validates_tool_result_and_falls_back(self, response, expected_fallback):
        client = ClientStub(tools=[response])
        role = make_role(client=client)
        state = make_state()

        text = await role.speak(state, ConversationLog(), "day_speech")

        assert (len(text) >= 15) is expected_fallback
        assert text

    @pytest.mark.asyncio
    async def test_speak_rejects_dead_or_wrong_phase_player(self):
        role = make_role()
        state = make_state()
        state.players[1].is_alive = False
        assert await role.speak(state, ConversationLog(), "day_speech") is None

        state2 = make_state(phase=GamePhase.NIGHT)
        assert await role.speak(state2, ConversationLog(), "day_speech") is None

    @pytest.mark.asyncio
    async def test_last_words_validation_paths(self):
        client = ClientStub(tools=[tool_msg("last_words", "我已经出局了，希望好人能找出最后的狼人。")])
        role = make_role(client=client)
        log = ConversationLog()

        # Eligible: exile, any round
        state = make_state(phase=GamePhase.LAST_WORDS)
        state.death_history = [__import__("app.models.actions", fromlist=["DeathReport"]).DeathReport(1, "exile", 2)]
        state.players[1].is_alive = False
        text = await role.speak(state, log, "last_words")
        assert text and "出局" in text

        # Already used
        assert await role.speak(state, log, "last_words") is None

        # No death record
        fresh = make_state(phase=GamePhase.LAST_WORDS)
        assert await BaseRole(1, "wolf-killer-villager", PromptBuilder(), ClientStub()).speak(
            fresh, log, "last_words",
        ) is None

        # Ineligible cause: round-2 night death
        from app.models.actions import DeathReport
        ineligible = make_state(phase=GamePhase.LAST_WORDS)
        ineligible.death_history = [DeathReport(1, "wolf_kill", 2)]
        ineligible.players[1].is_alive = False
        assert await BaseRole(1, "wolf-killer-villager", PromptBuilder(), ClientStub()).speak(
            ineligible, log, "last_words",
        ) is None

    @pytest.mark.asyncio
    async def test_fallback_speech_covers_last_words_and_alone_cases(self):
        role = make_role()
        log = ConversationLog()
        state = make_state(phase=GamePhase.LAST_WORDS, seats=2)
        from app.models.actions import DeathReport
        state.death_history = [DeathReport(1, "exile", 1)]
        state.players[1].is_alive = False
        fallback = role._generate_fallback_speech(state, "last_words")
        assert "1号" in fallback and "出局" in fallback

        alone = make_state(phase=GamePhase.LAST_WORDS, seats=1)
        alone.death_history = [DeathReport(1, "exile", 1)]
        alone.players[1].is_alive = False
        fallback_alone = role._generate_fallback_speech(alone, "last_words")
        assert "1号" in fallback_alone

        day_alone = make_state(seats=1)
        fallback_day = role._generate_fallback_speech(day_alone, "day_speech")
        assert "1号" in fallback_day

    @pytest.mark.asyncio
    async def test_speak_exception_falls_back_inline(self):
        client = ClientStub(tools=[RuntimeError("network down")])
        role = make_role(client=client)
        state = make_state()

        text = await role.speak(state, ConversationLog(), "day_speech")

        assert text is not None and len(text) >= 15

    @pytest.mark.asyncio
    async def test_vote_parses_json_response(self):
        client = ClientStub(plain=[AIMessage(content='{"target_seat": 2, "reasoning": "可疑"}')])
        role = make_role(client=client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        vote = await role.vote(state, ConversationLog(), "exile_vote")

        assert isinstance(vote, VoteAction)
        assert vote.target_seat == 2

    @pytest.mark.asyncio
    async def test_vote_with_unparseable_response_abstains(self):
        client = ClientStub(plain=[AIMessage(content="随便说点话，没有JSON。")])
        role = make_role(client=client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        vote = await role.vote(state, ConversationLog(), "exile_vote")

        assert vote.target_seat is None

    @pytest.mark.asyncio
    async def test_request_action_rejects_unsupported_contract(self):
        from app.models.contracts import ActionContract
        role = make_role()
        request = ActionRequest(
            actor_seat=1, role_id="wolf-killer-villager",
            contract=ActionContract(
                "night_action", GamePhase.NIGHT, ("kill",), frozenset({"kill"}), 1, "pass",
            ),
            phase=GamePhase.NIGHT, round_id=1, idempotency_key="k",
        )
        with pytest.raises(ValueError, match="unsupported contract"):
            await role.request_action(make_state(), ConversationLog(), request)


class TestBaseRoleProperties:
    def test_is_good_and_skills(self):
        good = BaseRole(1, "wolf-killer-villager", PromptBuilder(), ClientStub())
        bad = BaseRole(1, "wolf-killer-werewolf", PromptBuilder(), ClientStub())
        assert good.is_good is True
        assert bad.is_good is False
        assert good.get_skills() == []

    @pytest.mark.asyncio
    async def test_speak_plain_context_uses_text_model(self):
        client = ClientStub(plain=[AIMessage(content="这是普通发言，没有工具调用也可以。")])
        role = make_role(client=client)
        state = make_state()

        text = await role.speak(state, ConversationLog(), "other_context")

        assert "普通发言" in text
        assert len(client.plain_model.messages) == 1

    @pytest.mark.asyncio
    async def test_speak_with_tools_skips_prevalidation_for_unknown_context(self):
        client = ClientStub(tools=[tool_msg("speak", "直接调用工具，绕过了预校验。")])
        role = make_role(client=client)
        state = make_state(phase=GamePhase.NIGHT)

        text = await role._speak_with_tools(state, "prompt", "unknown_context", ConversationLog())

        assert "绕过了预校验" in text

    @pytest.mark.asyncio
    async def test_speak_with_tools_unexpected_validation_error_falls_back(self):
        client = ClientStub(tools=[tool_msg("speak", "我认为三号玩家的发言非常可疑，值得重点关注。")])
        role = make_role(client=client)
        role._validate_tool_result = MagicMock(side_effect=RuntimeError("internal"))
        state = make_state()

        text = await role._speak_with_tools(state, "prompt", "day_speech", ConversationLog())

        assert text is not None and len(text) >= 15

    @pytest.mark.asyncio
    async def test_last_words_wrong_function_name_falls_back(self):
        client = ClientStub(tools=[tool_msg("speak", "我是1号，我已经出局了，大家要加油。")])
        role = make_role(client=client)
        state = make_state(phase=GamePhase.LAST_WORDS)
        from app.models.actions import DeathReport
        state.death_history = [DeathReport(1, "exile", 1)]
        state.players[1].is_alive = False

        text = await role.speak(state, ConversationLog(), "last_words")

        assert text
        assert "我怀疑的玩家" in text or "好人阵营" in text  # fallback, not the wrong-fn text
        assert len(text) >= 15

    def test_fallback_speech_unknown_role_uses_generic_name(self):
        role = BaseRole(1, "unknown-role", PromptBuilder(), ClientStub())
        state = make_state(phase=GamePhase.LAST_WORDS, seats=1)
        from app.models.actions import DeathReport
        state.death_history = [DeathReport(1, "exile", 1)]
        state.players[1].is_alive = False
        fallback = role._generate_fallback_speech(state, "last_words")
        assert "玩家" in fallback

    def test_speak_validation_requires_existing_player(self):
        role = make_role()
        state = make_state()
        state.players = {}
        valid, reason = role._validate_speak(state)
        assert valid is False and "不存在" in reason

    def test_last_words_validation_paths(self):
        role = make_role()
        from app.models.actions import DeathReport

        state = make_state(phase=GamePhase.LAST_WORDS)
        state.players = {}
        valid, reason = role._validate_last_words(state)
        assert valid is False and "不存在" in reason

        state.players = {1: PlayerState(1, "wolf-killer-villager", "good")}
        state.death_history = [DeathReport(2, "exile", 1)]  # no record for seat 1
        valid, reason = role._validate_last_words(state)
        assert valid is False and "尚未出局" in reason

        state.death_history = [DeathReport(1, "wolf_kill", 2)]  # ineligible cause/round
        valid, reason = role._validate_last_words(state)
        assert valid is False and "不符合遗言条件" in reason

    @pytest.mark.asyncio
    async def test_invoke_json_action_rejects_non_text_content(self):
        role = make_role()
        response = MagicMock()
        response.content = 123
        client = MagicMock()
        client.get_model.return_value.ainvoke = AsyncMock(return_value=response)
        role.llm_client = client
        from app.core.action_validator import ActionValidationError
        with pytest.raises(ActionValidationError, match="text"):
            await role._invoke_json_action([], object())

    @pytest.mark.asyncio
    async def test_invoke_strict_action_raises_mapped_capability_error(self):
        from unittest.mock import patch
        from app.agents.output_parser import StrictCapabilityError
        role = make_role()
        client = MagicMock()
        client.get_model_with_action_tool.return_value.ainvoke = AsyncMock(side_effect=RuntimeError("raw"))
        role.llm_client = client
        with patch("app.roles.base.LLMClient.map_strict_capability_error",
                   return_value=StrictCapabilityError("mapped")) as mapped:
            with pytest.raises(StrictCapabilityError, match="mapped"):
                await role._invoke_strict_action([], object())
        mapped.assert_called_once()


class TestBaseRoleAccept:
    def _vote_request(self, state) -> ActionRequest:
        return ActionRequest(
            actor_seat=1, role_id="wolf-killer-villager", contract=VOTE_CONTRACT,
            phase=GamePhase.VOTE_CASTING, round_id=state.round_number,
            idempotency_key="1:vote_casting:1:1:exile_vote",
        )

    @pytest.mark.asyncio
    async def test_vote_request_emits_non_secret_telemetry(self):
        class TelemetryLogger:
            def __init__(self):
                self.records = []

            def log_vote_telemetry(self, game_id, round_num, seat, **data):
                self.records.append((game_id, round_num, seat, data))

        client = ClientStub(strict=[AIMessage(
            content="",
            tool_calls=[{
                "name": "exile_vote",
                "args": {"action_type": "vote", "target_seat": 2, "reasoning": "private"},
                "id": "call_1",
            }],
        )])
        logger = TelemetryLogger()
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        await role.request_action(state, ConversationLog(logger=logger, game_id="g"), self._vote_request(state))

        assert len(logger.records) == 1
        game_id, round_num, seat, data = logger.records[0]
        assert (game_id, round_num, seat) == ("g", 1, 1)
        assert data["transport"] == "strict"
        assert data["attempt"] == 1
        assert data["retried"] is False
        assert data["parse_result"] == "accepted"
        assert data["prompt_chars"] > 0 and data["elapsed_ms"] >= 0
        assert "reasoning" not in data and "response" not in data

    @pytest.mark.asyncio
    async def test_vote_timeout_records_safe_non_secret_telemetry(self):
        class TelemetryLogger:
            def __init__(self):
                self.records = []

            def log_vote_telemetry(self, game_id, round_num, seat, **data):
                self.records.append(data)

        class SlowModel:
            async def ainvoke(self, messages):
                await asyncio.sleep(0.05)

        class SlowClient:
            action_timeout_seconds = 0.001

            def get_model_with_action_tool(self, contract):
                return SlowModel()

            def get_model(self):
                return SlowModel()

        logger = TelemetryLogger()
        state = make_state(phase=GamePhase.VOTE_CASTING)
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), SlowClient())

        command = await role.request_action(
            state, ConversationLog(logger=logger, game_id="g"), self._vote_request(state),
        )

        assert command.command.action_type == "abstain"
        assert len(logger.records) == 1
        assert logger.records[0]["transport"] == "strict"
        assert logger.records[0]["attempt"] == 1
        assert logger.records[0]["retried"] is False
        assert logger.records[0]["parse_result"] == "timeout_fallback"

    @pytest.mark.asyncio
    async def test_accept_command_records_commit_through_applier(self):
        client = ClientStub(strict=[AIMessage(
            content="",
            tool_calls=[{
                "name": "exile_vote",
                "args": {"action_type": "vote", "target_seat": 2, "reasoning": "x"},
                "id": "call_1",
            }],
        )])
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        accepted = await role.request_action(state, ConversationLog(), self._vote_request(state))

        assert accepted.command.action_type == "vote"
        assert accepted.command.target_seat == 2
        assert "1:vote_casting:1:1:exile_vote" in state._pipeline_runtime.commits
        assert state._pipeline_runtime.revision == 1

    @pytest.mark.asyncio
    async def test_accept_command_rejects_dead_target_and_falls_back(self):
        client = ClientStub(strict=[
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "exile_vote",
                    "args": {"action_type": "vote", "target_seat": 2, "reasoning": "x"},
                    "id": "call_1",
                }],
            ),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "exile_vote",
                    "args": {"action_type": "vote", "target_seat": 2, "reasoning": "x"},
                    "id": "call_2",
                }],
            ),
        ])
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)
        state.players[2].is_alive = False

        accepted = await role.request_action(state, ConversationLog(), self._vote_request(state))

        assert accepted.command.action_type == "abstain"
        assert accepted.command.target_seat is None

    @pytest.mark.asyncio
    async def test_accept_command_rejects_disallowed_action_type(self):
        client = ClientStub(strict=[AIMessage(
            content="",
            tool_calls=[{
                "name": "exile_vote",
                "args": {"action_type": "kill", "target_seat": 2, "reasoning": "x"},
                "id": "call_1",
            }],
        )])
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        with pytest.raises(ActionValidationError):
            await role._accept_command(
                state, self._vote_request(state),
                ActionCommand(action_type="kill", target_seat=2, reasoning="x"),
            )

    def test_accept_command_requires_alive_actor(self):
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), ClientStub())
        state = make_state(phase=GamePhase.VOTE_CASTING)
        state.players[1].is_alive = False

        with pytest.raises(ActionValidationError, match="alive"):
            role._accept_command(
                state, self._vote_request(state),
                ActionCommand(action_type="vote", target_seat=2, reasoning="x"),
            )
