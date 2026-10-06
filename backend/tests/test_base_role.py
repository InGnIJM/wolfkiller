import asyncio
import time

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import AIMessage
from openai import APIConnectionError, APITimeoutError, RateLimitError

from app.agents.llm_client import StructuredResponse
from app.agents.prompt_builder import PromptBuilder
from app.agents.retry import RetryPolicy
from app.core.action_validator import ActionValidationError
from app.core.conversation_log import ConversationLog
from app.core.game_engine import VOTE_CONTRACT
from app.models.actions import DeathReport, VoteAction, is_last_words_eligible
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
    def __init__(
        self, plain=(), tools=(), strict=(), *, supports_strict_actions=True,
        supports_action_tools=None,
        action_timeout_seconds=90.0, action_retry_timeout_seconds=60.0,
    ):
        self.plain_model = ModelStub(plain)
        self.tools_model = ModelStub(tools)
        self.strict_model = ModelStub(strict)
        self.supports_strict_actions = supports_strict_actions
        self.supports_action_tools = (
            supports_strict_actions
            if supports_action_tools is None else supports_action_tools
        )
        self.action_timeout_seconds = action_timeout_seconds
        self.action_retry_timeout_seconds = action_retry_timeout_seconds

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
    @pytest.mark.parametrize(("cause", "round_number", "expected"), [
        ("wolf_kill", 1, True),
        ("poison", 1, True),
        ("exile", 2, True),
        ("hunter_shot", 1, False),
        ("self_explode", 1, False),
        ("love_death", 1, False),
        ("other", 1, False),
        ("wolf_kill", 2, False),
        ("poison", 2, False),
    ])
    def test_last_words_eligibility_is_cause_specific(self, cause, round_number, expected):
        assert is_last_words_eligible(cause, round_number) is expected

    @pytest.mark.parametrize(("cause", "expected"), [
        ("knight_duel", True),
        ("exile", True),
        ("self_explode", False),
        ("wolf_kill", False),
        ("poison", False),
        ("other", False),
    ])
    def test_daytime_last_words_are_limited_to_the_duel_victim(self, cause, expected):
        # A daytime skill death only earns last words on the interrupted-day
        # path; the night path never passes ``daytime``, which is what keeps
        # the knight's own ``knight_duel`` death silent.
        assert is_last_words_eligible(cause, 2, daytime=True) is expected

    @pytest.mark.asyncio
    async def test_day_speech_uses_one_context_specific_gateway_tool(self):
        class GatewayClient:
            action_timeout_seconds = 90.0
            action_retry_timeout_seconds = 60.0

            def __init__(self):
                self.calls = []

            async def ainvoke_json(self, messages, *, tool_name, schema):
                self.calls.append((messages, tool_name, schema))
                return StructuredResponse(
                    payload={"text": "我认为三号玩家的发言存在矛盾，需要继续关注。"},
                    transport="tool",
                    has_tool_calls=True,
                )

        client = GatewayClient()
        role = make_role(client=client)

        text = await role.speak(make_state(), ConversationLog(), "day_speech")

        assert text.startswith("我认为三号玩家")
        assert len(client.calls) == 1
        _, tool_name, schema = client.calls[0]
        assert tool_name == "speak"
        assert schema["required"] == ["text"]
        assert "last_words" not in str(client.calls[0])

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
    @pytest.mark.parametrize("cause", ["hunter_shot", "self_explode", "love_death"])
    async def test_last_words_rejects_ineligible_cause_before_model_invocation(self, cause):
        client = ClientStub(tools=[tool_msg("last_words", "这段内容不应被模型生成。")])
        role = make_role(client=client)
        state = make_state(phase=GamePhase.LAST_WORDS)
        state.death_history = [DeathReport(1, cause, 1)]
        state.players[1].is_alive = False

        assert await role.speak(state, ConversationLog(), "last_words") is None
        assert client.tools_model.messages == []

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
    async def test_speak_times_out_locally_then_falls_back(self):
        class HangingModel:
            async def ainvoke(self, messages):
                await asyncio.sleep(10)
                raise AssertionError("provider hang must be cut by local deadline")

        class HangingClient(ClientStub):
            def __init__(self):
                super().__init__(action_timeout_seconds=0.02)
                self.tools_model = HangingModel()

        role = make_role(client=HangingClient())
        text = await asyncio.wait_for(
            role.speak(make_state(), ConversationLog(), "day_speech"),
            timeout=1.0,
        )

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

    def test_fallback_speech_never_names_the_role(self):
        """A failed call must not turn into a public identity reveal.

        The template used to splice in ``RoleSpec.display_name``, so a fallback
        last words read 「我是12号Wolf King，我出局了」 in games configured to keep
        roles secret, and every later prompt quoted it back at the table.
        """
        specs = builtin_registry.freeze().specs
        names = {spec.display_name for spec in specs.values()}
        assert names, "registry has no display names to check against"
        for role_id in specs:
            role = BaseRole(1, role_id, PromptBuilder(), ClientStub())
            state = make_state(phase=GamePhase.LAST_WORDS, seats=2)
            state.death_history = [DeathReport(1, "exile", 1)]
            state.players[1].is_alive = False
            fallback = role._generate_fallback_speech(state, "last_words")
            assert "1号" in fallback
            for name in names:
                assert name not in fallback, (role_id, name, fallback)

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

    @pytest.mark.parametrize(("phase", "expected"), [
        (GamePhase.SPEECH, True),
        (GamePhase.VOTE_CASTING, True),
        (GamePhase.VOTE_RESOLUTION, True),
        (GamePhase.LAST_WORDS, False),
        (GamePhase.NIGHT, False),
    ])
    def test_knight_duel_last_words_depend_on_the_daytime_phase(self, phase, expected):
        # The duel victim and the knight die of the same cause, so the phase is
        # what separates them: a daytime phase means a daytime death and grants
        # the victim words, while LAST_WORDS only ever announces night deaths
        # and therefore refuses the knight.
        from app.models.actions import DeathReport

        role = make_role()
        state = make_state(phase=phase)
        state.players = {1: PlayerState(1, "wolf-killer-villager", "good", is_alive=False)}
        state.death_history = [DeathReport(1, "knight_duel", 2)]

        valid, _reason = role._validate_last_words(state)

        assert valid is expected

    def test_sheriff_campaign_validation_paths(self):
        role = make_role()
        state = make_state(phase=GamePhase.SHERIFF_ELECTION)
        state.players = {}
        valid, reason = role._validate_sheriff_campaign(state)
        assert valid is False and "不存在" in reason
        state.players = {1: PlayerState(1, "wolf-killer-villager", "good", is_alive=False)}
        valid, reason = role._validate_sheriff_campaign(state)
        assert valid is False and "已出局" in reason
        state.players[1].is_alive = True
        state.phase = GamePhase.SPEECH
        valid, reason = role._validate_sheriff_campaign(state)
        assert valid is False and "不是警长竞选" in reason
        state.phase = GamePhase.SHERIFF_ELECTION
        valid, reason = role._validate_sheriff_campaign(state)
        assert valid is True

    @pytest.mark.asyncio
    async def test_sheriff_campaign_speak_and_short_text_fallback(self):
        client = ClientStub(tools=[tool_msg("speak", "我认为今晚的上警顺序值得认真讨论一下。")])
        role = make_role(client=client)
        state = make_state(phase=GamePhase.SHERIFF_ELECTION)
        text = await role.speak(state, ConversationLog(), "sheriff_campaign")
        assert "上警" in text
        short = ClientStub(tools=[tool_msg("speak", "过")])
        role = make_role(client=short)
        fallback = await role.speak(state, ConversationLog(), "sheriff_campaign")
        assert len(fallback) >= 15
        assert await role.speak(make_state(), ConversationLog(), "sheriff_campaign") is None
        wrong = ClientStub(tools=[tool_msg("last_words", "这段不该当作竞选发言通过。")])
        role = make_role(client=wrong)
        swapped = await role.speak(state, ConversationLog(), "sheriff_campaign")
        assert len(swapped) >= 15

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
    async def test_provider_without_strict_tools_uses_native_action_tool(self):
        client = ClientStub(
            strict=[AIMessage(
                content="",
                tool_calls=[{
                    "name": "exile_vote",
                    "args": {
                        "action_type": "vote",
                        "target_seat": 2,
                        "reasoning": "x",
                    },
                    "id": "call_1",
                }],
            )],
            supports_strict_actions=False,
            supports_action_tools=True,
        )
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        accepted = await role.request_action(
            state, ConversationLog(), self._vote_request(state),
        )

        assert accepted.command.target_seat == 2
        assert len(client.strict_model.messages) == 1
        assert client.plain_model.messages == []

    @pytest.mark.asyncio
    async def test_json_action_prefers_dedicated_action_model(self):
        class ActionClient:
            supports_strict_actions = False
            action_timeout_seconds = 90.0
            action_retry_timeout_seconds = 60.0

            def __init__(self):
                self.plain = ModelStub([RuntimeError("plain model must not run")])
                self.action = ModelStub([AIMessage(content=(
                    '{"action_type":"vote","target_seat":2,"reasoning":"x"}'
                ))])

            def get_model(self):
                return self.plain

            def get_action_model(self):
                return self.action

        client = ActionClient()
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        accepted = await role.request_action(
            state, ConversationLog(), self._vote_request(state),
        )

        assert accepted.command.target_seat == 2
        assert len(client.action.messages) == 1
        assert client.plain.messages == []

    @pytest.mark.asyncio
    async def test_primary_json_timeout_retries_once_with_retry_timeout(self):
        class RetryModel:
            def __init__(self):
                self.calls = 0

            async def ainvoke(self, messages):
                self.calls += 1
                if self.calls == 1:
                    await asyncio.sleep(0.05)
                return AIMessage(content=(
                    '{"action_type":"vote","target_seat":2,"reasoning":"x"}'
                ))

        class RetryClient:
            supports_strict_actions = False
            action_timeout_seconds = 0.001
            action_retry_timeout_seconds = 0.1

            def __init__(self):
                self.model = RetryModel()

            def get_model(self):
                return self.model

        client = RetryClient()
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        accepted = await role.request_action(
            state, ConversationLog(), self._vote_request(state),
        )

        assert accepted.command.target_seat == 2
        assert client.model.calls == 2

    @pytest.mark.asyncio
    async def test_timeout_retry_uses_compact_vote_messages(self):
        class RetryModel:
            def __init__(self):
                self.messages = []

            async def ainvoke(self, messages):
                self.messages.append(messages)
                if len(self.messages) == 1:
                    await asyncio.sleep(0.05)
                return AIMessage(content=(
                    '{"action_type":"vote","target_seat":2,"reasoning":"x"}'
                ))

        class RetryClient:
            supports_strict_actions = False
            action_timeout_seconds = 0.001
            action_retry_timeout_seconds = 0.1

            def __init__(self):
                self.model = RetryModel()

            def get_action_model(self):
                return self.model

        client = RetryClient()
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)
        conversation_log = ConversationLog()
        for index in range(20):
            conversation_log.add_public_speech(
                2, "wolf-killer-villager", f"old-history-{index}-" + "x" * 300,
                0, "speech",
            )
        conversation_log.add_public_speech(
            2, "wolf-killer-villager", "current-round-evidence", 1, "speech",
        )

        accepted = await role.request_action(
            state, conversation_log, self._vote_request(state),
        )

        first_chars = sum(len(message.content) for message in client.model.messages[0])
        retry_chars = sum(len(message.content) for message in client.model.messages[1])
        assert accepted.command.target_seat == 2
        assert "current-round-evidence" in client.model.messages[1][1].content
        assert "old-history-0" not in client.model.messages[1][1].content
        assert retry_chars < first_chars * 0.75

    @pytest.mark.asyncio
    async def test_final_retry_adds_format_tail_and_uses_short_deadline(self, monkeypatch):
        deadlines = []

        real_wait_for = asyncio.wait_for

        async def spy_wait_for(coro, timeout):
            deadlines.append(timeout)
            return await real_wait_for(coro, timeout=timeout)

        monkeypatch.setattr(asyncio, "wait_for", spy_wait_for)

        class FinalModel:
            def __init__(self):
                self.messages = []
                self.calls = 0

            async def ainvoke(self, messages):
                self.calls += 1
                self.messages.append(messages)
                if self.calls < 3:
                    await asyncio.sleep(0.05)
                return AIMessage(content=(
                    '{"action_type":"vote","target_seat":2,"reasoning":"x"}'
                ))

        class FinalClient:
            supports_strict_actions = False
            action_timeout_seconds = 0.001
            action_retry_timeout_seconds = 0.001
            action_final_retry_timeout_seconds = 5.0

            def __init__(self):
                self.model = FinalModel()

            def get_action_model(self):
                return self.model

        client = FinalClient()
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        accepted = await role.request_action(
            state, ConversationLog(), self._vote_request(state),
        )

        assert accepted.command.target_seat == 2
        assert deadlines == [0.001, 0.001, 5.0]
        final_messages = client.model.messages[2]
        assert final_messages[-1].content.startswith("The previous attempts failed")
        assert "exactly one JSON" in final_messages[-1].content

    @pytest.mark.asyncio
    async def test_strict_timeout_retries_json_without_writing_state(self):
        class SlowStrictModel:
            def __init__(self):
                self.calls = 0

            async def ainvoke(self, messages):
                self.calls += 1
                await asyncio.sleep(0.05)

        class StrictThenJsonClient:
            supports_strict_actions = True
            action_timeout_seconds = 0.001
            action_retry_timeout_seconds = 0.1

            def __init__(self):
                self.strict = SlowStrictModel()
                self.action = ModelStub([AIMessage(content=(
                    '{"action_type":"vote","target_seat":2,"reasoning":"x"}'
                ))])

            def get_model_with_action_tool(self, contract):
                return self.strict

            def get_action_model(self):
                return self.action

        client = StrictThenJsonClient()
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        accepted = await role.request_action(
            state, ConversationLog(), self._vote_request(state),
        )

        assert accepted.command.target_seat == 2
        assert client.strict.calls == 1
        assert len(client.action.messages) == 1
        assert not hasattr(state, "_pipeline_runtime")

    @pytest.mark.asyncio
    async def test_json_action_cancellation_propagates_without_commit(self):
        started = asyncio.Event()
        never = asyncio.Event()

        class CancellableModel:
            async def ainvoke(self, messages):
                started.set()
                await never.wait()

        class CancellableClient:
            supports_strict_actions = False
            action_timeout_seconds = 90.0
            action_retry_timeout_seconds = 60.0

            def get_action_model(self):
                return CancellableModel()

        role = BaseRole(
            1, "wolf-killer-villager", PromptBuilder(), CancellableClient(),
        )
        state = make_state(phase=GamePhase.VOTE_CASTING)
        task = asyncio.create_task(role.request_action(
            state, ConversationLog(), self._vote_request(state),
        ))
        await asyncio.wait_for(started.wait(), timeout=0.1)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        runtime = getattr(state, "_pipeline_runtime", None)
        assert runtime is None or runtime.revision == 0

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
            action_retry_timeout_seconds = 0.001
            action_final_retry_timeout_seconds = 0.001

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
        assert len(logger.records) == 3
        assert logger.records[0]["transport"] == "strict"
        assert logger.records[0]["attempt"] == 1
        assert logger.records[0]["retried"] is False
        assert logger.records[0]["parse_result"] == "timeout_retry"
        assert logger.records[1]["transport"] == "json"
        assert logger.records[1]["attempt"] == 2
        assert logger.records[1]["retried"] is True
        assert logger.records[1]["parse_result"] == "timeout_retry"
        assert logger.records[2]["transport"] == "json"
        assert logger.records[2]["attempt"] == 3
        assert logger.records[2]["retried"] is True
        assert logger.records[2]["parse_result"] == "timeout_fallback"

    @pytest.mark.asyncio
    async def test_provider_timeouts_retry_then_technical_abstain(self):
        timeout = APITimeoutError(request=httpx.Request("POST", "https://model.test"))
        client = ClientStub(
            plain=[timeout, timeout, timeout], supports_strict_actions=False,
        )
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        accepted = await role.request_action(
            state, ConversationLog(), self._vote_request(state),
        )

        assert accepted.command.action_type == "abstain"
        assert accepted.command.reasoning == "系统超时，代投弃权"
        assert len(client.plain_model.messages) == 3

    @pytest.mark.asyncio
    async def test_accept_command_only_returns_validated_decision(self):
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
        assert not hasattr(state, "_pipeline_runtime")

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
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "exile_vote",
                    "args": {"action_type": "vote", "target_seat": 2, "reasoning": "x"},
                    "id": "call_3",
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

    @pytest.mark.asyncio
    async def test_strict_transport_salvages_json_content_without_a_second_call(self):
        """A gateway that binds the action tool without forcing it leaves the
        model free to answer in the content block. That JSON is exactly what the
        JSON transport would ask for next, so it must be accepted on the spot
        instead of paying a whole extra round-trip for it."""
        class TelemetryLogger:
            def __init__(self):
                self.records = []

            def log_vote_telemetry(self, game_id, round_num, seat, **data):
                self.records.append(data)

        class JsonInContentClient:
            supports_strict_actions = True
            action_timeout_seconds = 5.0
            action_retry_timeout_seconds = 5.0
            action_final_retry_timeout_seconds = 5.0

            def __init__(self):
                self.strict = ModelStub([AIMessage(content=(
                    '{"action_type":"vote","target_seat":2,"reasoning":"salvaged"}'
                ))])
                # Left empty on purpose: reaching for it would pop from an empty
                # list and fail the test loudly.
                self.action = ModelStub([])

            def get_model_with_action_tool(self, contract):
                return self.strict

            def get_action_model(self):
                return self.action

        client = JsonInContentClient()
        logger = TelemetryLogger()
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        accepted = await role.request_action(
            state, ConversationLog(logger=logger, game_id="g"),
            self._vote_request(state),
        )

        assert accepted.command.target_seat == 2
        assert accepted.command.reasoning == "salvaged"
        assert accepted.technical_failure_code is None
        assert client.action.messages == []
        assert len(logger.records) == 1
        assert logger.records[0]["transport"] == "strict"
        assert logger.records[0]["attempt"] == 1
        assert logger.records[0]["parse_result"] == "strict_content_fallback"
        # game_summary counts a non-null failure_code as an error, so a salvaged
        # call must not carry one — it succeeded.
        assert logger.records[0]["failure_code"] is None

    @pytest.mark.asyncio
    async def test_strict_transport_still_switches_to_json_for_prose_content(self):
        """Content that is not a valid issued action keeps the old behaviour:
        StrictCapabilityError, then the JSON transport gets its turn."""
        class ProseThenJsonClient:
            supports_strict_actions = True
            action_timeout_seconds = 5.0
            action_retry_timeout_seconds = 5.0
            action_final_retry_timeout_seconds = 5.0

            def __init__(self):
                self.strict = ModelStub([AIMessage(
                    content="我认为应该投2号，因为他的发言前后矛盾。"
                )])
                self.action = ModelStub([AIMessage(content=(
                    '{"action_type":"vote","target_seat":2,"reasoning":"json"}'
                ))])

            def get_model_with_action_tool(self, contract):
                return self.strict

            def get_action_model(self):
                return self.action

        client = ProseThenJsonClient()
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        accepted = await role.request_action(
            state, ConversationLog(), self._vote_request(state),
        )

        assert accepted.command.reasoning == "json"
        assert len(client.strict.messages) == 1
        assert len(client.action.messages) == 1
        assert role._action_tool_content_salvaged is False


    @pytest.mark.asyncio
    async def test_transient_failure_keeps_the_native_transport(self):
        """A connection reset says nothing about the request shape, so the retry
        must re-ask the same way instead of spending itself on a new prompt for
        the same broken pipe."""
        class TelemetryLogger:
            def __init__(self):
                self.records = []

            def log_vote_telemetry(self, game_id, round_num, seat, **data):
                self.records.append(data)

        class FlakyStrictClient:
            supports_strict_actions = True
            action_timeout_seconds = 5.0
            action_retry_timeout_seconds = 5.0
            action_final_retry_timeout_seconds = 5.0
            action_retry_backoff_seconds = 0.0

            def __init__(self):
                self.strict = ModelStub([
                    APIConnectionError(
                        request=httpx.Request("POST", "https://model.test/v1"),
                    ),
                    AIMessage(content="", tool_calls=[{
                        "name": "exile_vote",
                        "args": {
                            "action_type": "vote", "target_seat": 2,
                            "reasoning": "same-transport",
                        },
                        "id": "call_1",
                    }]),
                ])
                self.action = ModelStub([])

            def get_model_with_action_tool(self, contract):
                return self.strict

            def get_action_model(self):
                return self.action

        client = FlakyStrictClient()
        logger = TelemetryLogger()
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        accepted = await role.request_action(
            state, ConversationLog(logger=logger, game_id="g"),
            self._vote_request(state),
        )

        assert accepted.command.reasoning == "same-transport"
        assert client.action.messages == []
        assert [record["transport"] for record in logger.records] == [
            "strict", "strict",
        ]
        assert logger.records[0]["parse_result"] == "invoke_error_retry"
        assert logger.records[1]["parse_result"] == "accepted"

    @pytest.mark.asyncio
    async def test_transient_retry_actually_waits_before_re_asking(self):
        class FlakyClient:
            supports_strict_actions = False
            action_timeout_seconds = 5.0
            action_retry_timeout_seconds = 5.0
            action_final_retry_timeout_seconds = 5.0
            action_retry_backoff_seconds = 0.05

            def __init__(self):
                self.model = ModelStub([
                    APIConnectionError(
                        request=httpx.Request("POST", "https://model.test/v1"),
                    ),
                    AIMessage(content=(
                        '{"action_type":"vote","target_seat":2,"reasoning":"x"}'
                    )),
                ])

            def get_action_model(self):
                return self.model

        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), FlakyClient())
        state = make_state(phase=GamePhase.VOTE_CASTING)
        started = time.monotonic()

        accepted = await role.request_action(
            state, ConversationLog(), self._vote_request(state),
        )

        assert accepted.command.target_seat == 2
        assert time.monotonic() - started >= 0.04

    @pytest.mark.asyncio
    async def test_transient_failures_keep_the_transport_across_all_attempts(self):
        """Both retries of a transient fault stay on the native transport."""
        class TelemetryLogger:
            def __init__(self):
                self.records = []

            def log_vote_telemetry(self, game_id, round_num, seat, **data):
                self.records.append(data)

        class TwiceFlakyClient:
            supports_strict_actions = True
            action_timeout_seconds = 5.0
            action_retry_timeout_seconds = 5.0
            action_final_retry_timeout_seconds = 5.0
            action_retry_backoff_seconds = 0.0

            def __init__(self):
                down = lambda: APIConnectionError(  # noqa: E731
                    request=httpx.Request("POST", "https://model.test/v1"),
                )
                self.strict = ModelStub([
                    down(),
                    down(),
                    AIMessage(content="", tool_calls=[{
                        "name": "exile_vote",
                        "args": {
                            "action_type": "vote", "target_seat": 2,
                            "reasoning": "third-time",
                        },
                        "id": "call_1",
                    }]),
                ])
                self.action = ModelStub([])

            def get_model_with_action_tool(self, contract):
                return self.strict

            def get_action_model(self):
                return self.action

        client = TwiceFlakyClient()
        logger = TelemetryLogger()
        role = BaseRole(1, "wolf-killer-villager", PromptBuilder(), client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        accepted = await role.request_action(
            state, ConversationLog(logger=logger, game_id="g"),
            self._vote_request(state),
        )

        assert accepted.command.reasoning == "third-time"
        assert client.action.messages == []
        assert [record["transport"] for record in logger.records] == [
            "strict", "strict", "strict",
        ]
        assert [record["parse_result"] for record in logger.records] == [
            "invoke_error_retry", "invoke_error_retry", "accepted",
        ]
        assert [record["attempt"] for record in logger.records] == [1, 2, 3]

    def test_retry_backoff_honours_a_provider_retry_after_hint(self):
        class Client:
            action_retry_backoff_seconds = 0.0

        role = make_role(client=Client())
        hint = RateLimitError(
            "slow down",
            response=httpx.Response(
                429, request=httpx.Request("POST", "https://model.test/v1"),
                headers={"retry-after": "7"},
            ),
            body=None,
        )

        assert role._retry_backoff_seconds(1, hint) == 7.0

    def test_retry_backoff_ignores_a_nonsense_client_attribute(self):
        class Client:
            action_retry_backoff_seconds = "soon"

        role = make_role(client=Client())
        base = RetryPolicy().base_delay_seconds
        delay = role._retry_backoff_seconds(1, RuntimeError("no hint"))

        # Falls back to the policy default, jittered within +/-25%.
        assert base * 0.75 <= delay <= base * 1.25


class RecordingCapture:
    """Duck-typed stand-in for the prompt-capture sink."""

    def __init__(self):
        self.calls = []

    def record(self, messages, *, tool_schema=None, tool_name=None):
        self.calls.append({
            "messages": list(messages),
            "tool_schema": tool_schema,
            "tool_name": tool_name,
        })

    def user_text(self, index=0):
        return self.calls[index]["messages"][1].content


class TestPromptCaptureWiring:
    """The sink must see the text the model actually receives.

    Capturing the pre-rewrite prompt would silently poison the training corpus:
    the speech gateway appends a JSON fallback instruction, and the retry path
    appends an escalation notice, so both differ from the rendered prompt.
    """

    def _vote_request(self, state) -> ActionRequest:
        return ActionRequest(
            actor_seat=1, role_id="wolf-killer-villager", contract=VOTE_CONTRACT,
            phase=GamePhase.VOTE_CASTING, round_id=state.round_number,
            idempotency_key="1:vote_casting:1:1:exile_vote",
        )

    @pytest.mark.asyncio
    async def test_speak_capture_sees_the_rewritten_gateway_prompt(self):
        class GatewayClient:
            action_timeout_seconds = 90.0
            action_retry_timeout_seconds = 60.0

            async def ainvoke_json(self, messages, *, tool_name, schema):
                return StructuredResponse(
                    payload={"text": "我认为三号玩家的发言存在矛盾，需要继续关注。"},
                    transport="tool", has_tool_calls=True,
                )

        capture = RecordingCapture()
        role = make_role(client=GatewayClient())

        await role.speak(make_state(), ConversationLog(), "day_speech", capture=capture)

        assert len(capture.calls) == 1
        assert capture.user_text().endswith("不要添加解释或代码块。")
        assert "如果当前接口无法调用函数" in capture.user_text()
        assert capture.calls[0]["tool_name"] == "speak"
        assert capture.calls[0]["tool_schema"]["required"] == ["text"]

    @pytest.mark.asyncio
    async def test_speak_capture_records_the_bound_tool_schema(self):
        client = ClientStub(tools=[
            tool_msg("speak", "我认为三号玩家的发言非常可疑，值得重点关注。"),
        ])
        capture = RecordingCapture()
        role = make_role(client=client)

        await role.speak(make_state(), ConversationLog(), "day_speech", capture=capture)

        assert len(capture.calls) == 1
        assert capture.calls[0]["tool_name"] == "speak"
        assert capture.calls[0]["tool_schema"]["required"] == ["text"]
        # This transport binds the tools instead of rewriting the prompt.
        assert "如果当前接口无法调用函数" not in capture.user_text()

    @pytest.mark.asyncio
    async def test_speak_capture_records_each_attempt_including_the_retry(self):
        client = ClientStub(tools=[
            AIMessage(content="普通文本，没有调用函数。"),
            tool_msg("speak", "我认为三号玩家的发言非常可疑，值得重点关注。"),
        ])
        capture = RecordingCapture()
        role = make_role(client=client)

        await role.speak(make_state(), ConversationLog(), "day_speech", capture=capture)

        assert len(capture.calls) == 2
        assert "你刚才没有调用发言函数" not in capture.user_text(0)
        assert "你刚才没有调用发言函数" in capture.user_text(1)

    @pytest.mark.asyncio
    async def test_speak_capture_covers_non_tool_contexts(self):
        client = ClientStub(plain=[AIMessage(content="普通发言内容，长度足够通过校验。")])
        capture = RecordingCapture()
        role = make_role(client=client)

        await role.speak(make_state(), ConversationLog(), "werewolf_chat", capture=capture)

        assert len(capture.calls) == 1
        assert capture.calls[0]["tool_name"] is None
        assert capture.calls[0]["tool_schema"] is None

    @pytest.mark.asyncio
    async def test_json_transport_capture_records_no_tool_name(self):
        client = ClientStub(
            supports_strict_actions=False,
            plain=[AIMessage(
                content='{"action_type":"vote","target_seat":2,"reasoning":"x"}',
            )],
        )
        capture = RecordingCapture()
        role = make_role(client=client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        await role.request_action(
            state, ConversationLog(), self._vote_request(state), capture=capture,
        )

        # The JSON transport carries the schema inside the prompt itself.
        assert capture.calls[0]["tool_name"] is None
        assert capture.calls[0]["messages"][1].content

    @pytest.mark.asyncio
    async def test_strict_transport_capture_records_the_contract_tool_name(self):
        # The strict transport validates the payload, so this has to be a real
        # exile_vote command: ``tool_msg``'s ``{"text": ...}`` shape would fail
        # validation and spend an unseeded retry.
        client = ClientStub(
            supports_strict_actions=True,
            strict=[AIMessage(
                content="",
                tool_calls=[{
                    "name": "exile_vote",
                    "args": {"action_type": "vote", "target_seat": 2, "reasoning": "x"},
                    "id": "call_1",
                }],
            )],
        )
        capture = RecordingCapture()
        role = make_role(client=client)
        state = make_state(phase=GamePhase.VOTE_CASTING)

        await role.request_action(
            state, ConversationLog(), self._vote_request(state), capture=capture,
        )

        assert len(capture.calls) == 1
        assert capture.calls[0]["tool_name"] == VOTE_CONTRACT.resolved_tool_name

    @pytest.mark.asyncio
    async def test_speak_with_on_chunk_forwarding(self):
        chunks_received = []

        async def chunk_sink(text: str):
            chunks_received.append(text)

        class StreamingGatewayClient:
            action_timeout_seconds = 90.0
            action_retry_timeout_seconds = 60.0

            async def ainvoke_json(self, messages, *, tool_name, schema, on_chunk=None):
                if on_chunk:
                    await on_chunk("我是1号发言中")
                from app.agents.llm_client import StructuredResponse
                return StructuredResponse(
                    payload={"text": "我是1号发言中，这是我的发言内容。"},
                    transport="tool",
                    has_tool_calls=True,
                )

        role = make_role(client=StreamingGatewayClient())
        state = make_state(phase=GamePhase.SPEECH)
        speech = await role.speak(state, ConversationLog(), "day_speech", on_chunk=chunk_sink)
        assert speech == "我是1号发言中，这是我的发言内容。"
        assert chunks_received == ["我是1号发言中"]

    @pytest.mark.asyncio
    async def test_speak_with_on_chunk_type_error_fallback(self):
        class NonStreamingGatewayClient:
            action_timeout_seconds = 90.0
            action_retry_timeout_seconds = 60.0

            async def ainvoke_json(self, messages, *, tool_name, schema):
                from app.agents.llm_client import StructuredResponse
                return StructuredResponse(
                    payload={"text": "我是1号玩家，旧客户端不支持流式发言。"},
                    transport="tool",
                    has_tool_calls=True,
                )

        role = make_role(client=NonStreamingGatewayClient())
        state = make_state(phase=GamePhase.SPEECH)
        sink = AsyncMock()
        speech = await role.speak(state, ConversationLog(), "day_speech", on_chunk=sink)
        assert speech == "我是1号玩家，旧客户端不支持流式发言。"

    @pytest.mark.asyncio
    async def test_invoke_llm_and_tools_stream_branches(self):
        from langchain_core.messages import AIMessageChunk
        chunks_received = []

        async def chunk_sink(text: str):
            chunks_received.append(text)

        class StreamingModel:
            async def astream(self, messages):
                yield AIMessageChunk(content="流式发言内容测试")

            async def ainvoke(self, messages):
                return AIMessage(content="非流式")

        class StreamingToolModel:
            async def astream(self, messages):
                yield AIMessageChunk(
                    content="",
                    tool_call_chunks=[{"name": "speak", "args": '{"text": "模型流式工具输出"}', "id": "c1", "index": 0}],
                )

        class StreamingModelClient:
            action_timeout_seconds = 90.0
            action_retry_timeout_seconds = 60.0

            def __init__(self):
                from app.agents.llm_client import LLMClient, LLMClientConfig
                self._client = LLMClient(config=LLMClientConfig(
                    base_url="https://api.openai.com/v1",
                    api_key="key",
                    model_id="gpt-4o",
                    temperature=0.7,
                    max_tokens=512,
                    strict_base_url="https://api.openai.com/v1",
                    action_max_tokens=2048,
                ))

            def get_model(self):
                return StreamingModel()

            def get_model_with_tools(self, tools):
                return StreamingToolModel()

            async def _stream_and_accumulate(self, model, messages, on_chunk=None):
                return await self._client._stream_and_accumulate(model, messages, on_chunk=on_chunk)

        role = make_role(client=StreamingModelClient())
        # Test _invoke_llm with on_chunk
        res1 = await role._invoke_llm("prompt", on_chunk=chunk_sink)
        assert res1 == "流式发言内容测试"
        assert "流式发言内容测试" in chunks_received

        # Test _invoke_llm_with_tools with on_chunk
        res2 = await role._invoke_llm_with_tools(
            "prompt",
            [{"type": "function", "function": {"name": "speak", "parameters": {}}}, {"type": "function", "function": {"name": "other"}}],
            on_chunk=chunk_sink,
        )
        assert res2.arguments == {"text": "模型流式工具输出"}
