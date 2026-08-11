# 角色行动契约与规则一致性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 以可注册的角色行动契约统一提示词、模型输出和服务端结算，保证女巫单夜单药、狼人可自刀、两轮平票、动态人数/角色和安全的结构化输出。

**Architecture:** RoleSpec/ActionContract 是角色数量、行动阶段、Schema、语义校验和结算顺序的唯一规则源。模型只能返回 ActionCommand，引擎绑定行动者和阶段后经一个校验入口原子接受；strict 工具调用和 JSON 降级共用解析、校验、重试和安全放弃状态机。Prompt 只从契约和玩家可见状态生成任务，不包含目标策略白名单。

**Tech Stack:** Python 3、dataclasses、Pydantic v2、LangChain/OpenAI-compatible ChatOpenAI、pytest、pytest-asyncio、pytest-cov。

---

## 文件结构与边界

| 路径 | 职责 |
| --- | --- |
| backend/app/models/contracts.py（新建） | ActionCommand、ActionContract、RoleSpec、行动请求/接受记录及 Schema 生成。 |
| backend/app/roles/registry.py（新建） | 内置角色注册、角色配置校验、角色实例创建，不把角色名称分支留在引擎。 |
| backend/app/models/game.py | role_counts 配置、投票复投状态和行动幂等记录。 |
| backend/app/core/action_validator.py（新建） | 唯一 validate_and_accept 入口；只做服务端客观规则校验。 |
| backend/app/core/action_resolver.py | 只结算已接受的行动；不得再次绕过校验或消耗道具。 |
| backend/app/agents/output_parser.py | Pydantic 严格解析工具调用/JSON 成 ActionCommand。 |
| backend/app/agents/llm_client.py、backend/app/roles/base.py | strict → JSON 降级 → 一次纠错 → 安全放弃的统一调用状态机。 |
| backend/app/roles/werewolf.py、backend/app/roles/seer.py、backend/app/roles/witch.py、backend/app/core/game_engine.py | 仅保留统一行动入口、女巫单次行动、注册表夜间调度和平票状态。 |
| backend/app/agents/prompt_builder.py | 动态板子与契约任务，删除固定九人、策略白名单和长思维要求。 |
| backend/app/api/schemas.py、backend/app/services/game_service.py | 接受 role_counts、一次向后兼容转换、由注册表创建角色。 |

不在本计划内：REST/WebSocket 鉴权、回放脱敏、跨局 EventBus、前端构建和部署。即使测试暴露这些问题，也不得顺带修改。

### Task 1: 定义可注册行动契约

**Files:**

- Create: backend/app/models/contracts.py
- Create: backend/app/roles/registry.py
- Test: backend/tests/test_contracts.py

- [ ] **Step 1: 写失败测试，锁定 Schema、注册和动态角色约束。**

~~~python
def test_contract_schema_requires_action_type_target_and_reasoning():
    contract = builtin_registry.require("wolf-killer-werewolf").contracts[0]
    schema = contract.json_schema()
    assert schema["required"] == ["action_type", "target_seat", "reasoning"]
    assert schema["additionalProperties"] is False


def test_registry_rejects_unknown_role_and_count_mismatch():
    with pytest.raises(ValueError, match="unknown role"):
        builtin_registry.validate_role_counts({"unknown": 1}, player_count=1)
    with pytest.raises(ValueError, match="sum"):
        builtin_registry.validate_role_counts({"wolf-killer-villager": 1}, player_count=2)
~~~

- [ ] **Step 2: 运行测试，确认它因缺少模块失败。**

Run: python -m pytest tests/test_contracts.py -q

Expected: FAIL，提示无法导入 app.models.contracts。

- [ ] **Step 3: 实现最小契约和注册表。**

~~~python
@dataclass(frozen=True)
class ActionContract:
    contract_id: str
    phase: GamePhase
    action_types: tuple[str, ...]
    actions_requiring_target: frozenset[str]
    resolution_priority: int
    fallback_action_type: str

    def json_schema(self) -> dict:
        return {
            "type": "object", "additionalProperties": False,
            "properties": {
                "action_type": {"type": "string", "enum": list(self.action_types)},
                "target_seat": {"type": ["integer", "null"]},
                "reasoning": {"type": "string", "maxLength": 500},
            },
            "required": ["action_type", "target_seat", "reasoning"],
        }


@dataclass(frozen=True)
class RoleSpec:
    role_id: str
    camp: Camp
    role_factory: Callable[..., BaseRole]
    contracts: tuple[ActionContract, ...]


class ActionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_type: str
    target_seat: int | None
    reasoning: str


@dataclass(frozen=True)
class ActionRequest:
    actor_seat: int
    role_id: str
    contract: ActionContract
    phase: GamePhase
    round_id: int
    idempotency_key: str


@dataclass(frozen=True)
class AcceptedAction:
    request: ActionRequest
    command: ActionCommand
~~~

RoleRegistry.validate_role_counts() 必须检查角色已注册、数量是非负整数且总数等于 player_count；create_roles() 只能从通过校验的配置分配座位。内置注册 villager、werewolf、seer、witch、hunter；女巫只注册一个 witch_action，枚举为 save/poison/pass。
RoleRegistry.build_requests(state, roles, phase) 必须只为存活、拥有当前阶段契约且尚未接受同一行动键的座位创建 ActionRequest，并按 resolution_priority 排序。

- [ ] **Step 4: 运行通过测试。**

Run: python -m pytest tests/test_contracts.py -q

Expected: PASS。

- [ ] **Step 5: 提交。**

~~~bash
git add app/models/contracts.py app/roles/registry.py tests/test_contracts.py
git commit -m "feat(rules): add role action contracts"
~~~

### Task 2: 将配置和状态迁移为动态角色与复投状态

**Files:**

- Modify: backend/app/models/game.py:27-51,75-90
- Modify: backend/tests/test_models.py:9-39

- [ ] **Step 1: 写失败测试，覆盖非九人配置和投票状态。**

~~~python
def test_config_uses_role_counts_as_its_distribution():
    config = GameConfig(role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3})
    assert config.total_players == 4
    assert config.role_distribution() == ["wolf-killer-werewolf"] + ["wolf-killer-villager"] * 3


def test_game_state_tracks_tiebreak_round_and_abstentions():
    state = GameState(game_id="g")
    assert state.vote_round == 1
    assert state.is_tiebreak is False
    assert state.voted_seats == set()
~~~

- [ ] **Step 2: 运行失败测试。**

Run: python -m pytest tests/test_models.py -q

Expected: FAIL，GameConfig 不接受 role_counts，且 GameState 缺少复投字段。

- [ ] **Step 3: 实现唯一配置源与持久化状态。**

~~~python
@dataclass
class GameConfig:
    role_counts: dict[str, int] = field(default_factory=lambda: {
        "wolf-killer-werewolf": 3, "wolf-killer-villager": 3,
        "wolf-killer-seer": 1, "wolf-killer-witch": 1, "wolf-killer-hunter": 1,
    })

    @property
    def total_players(self) -> int:
        return sum(self.role_counts.values())

    def role_distribution(self) -> list[str]:
        return [role_id for role_id, count in self.role_counts.items() for _ in range(count)]
~~~

给 GameState 增加 vote_round=1、is_tiebreak=False、tiebreak_candidates=set()、supplemental_speakers=set()、voted_seats=set()、accepted_action_keys=set()。只在引擎开始新投票轮次时重置相应集合；重试/重连不得清空它们。

- [ ] **Step 4: 运行模型测试。**

Run: python -m pytest tests/test_models.py -q

Expected: PASS。

- [ ] **Step 5: 提交。**

~~~bash
git add app/models/game.py tests/test_models.py
git commit -m "feat(game): support dynamic role counts"
~~~

### Task 3: 建立唯一行动校验入口并收紧夜间结算

**Files:**

- Create: backend/app/core/action_validator.py
- Modify: backend/app/core/action_resolver.py:10-48,87-128
- Test: backend/tests/test_action_validator.py

- [ ] **Step 1: 写失败测试，固定客观合法性和幂等性。**

~~~python
def test_validator_accepts_wolf_self_kill_without_camp_whitelist(state, kill_request):
    accepted = validator.validate_and_accept(
        state, kill_request,
        {"action_type": "kill", "target_seat": 1, "reasoning": "x"},
    )
    assert accepted.command.target_seat == 1


def test_validator_rejects_save_not_matching_recorded_wolf_target(state, save_request):
    state.last_wolf_kill_target = 3
    with pytest.raises(ActionValidationError, match="wolf target"):
        validator.validate_and_accept(
            state, save_request,
            {"action_type": "save", "target_seat": 4, "reasoning": "x"},
        )


def test_validator_rejects_second_witch_action_in_same_night(state, witch_request):
    validator.validate_and_accept(
        state, witch_request,
        {"action_type": "pass", "target_seat": None, "reasoning": "x"},
    )
    with pytest.raises(ActionValidationError, match="already accepted"):
        validator.validate_and_accept(
            state, witch_request,
            {"action_type": "poison", "target_seat": 2, "reasoning": "x"},
        )
~~~

- [ ] **Step 2: 运行失败测试。**

Run: python -m pytest tests/test_action_validator.py -q

Expected: FAIL，找不到 ActionValidator。

- [ ] **Step 3: 实现校验器，并让 resolver 只消费已接受命令。**

~~~python
def validate_and_accept(self, state: GameState, request: ActionRequest, payload: dict) -> AcceptedAction:
    command = ActionCommand.model_validate(payload)
    self._validate_phase_and_actor(state, request)
    self._validate_idempotency(state, request)
    self._validate_target_shape(request.contract, command)
    self._validate_target_is_alive(state, command)
    self._validate_contract_semantics(state, request, command)
    state.accepted_action_keys.add(request.idempotency_key)
    self._reserve_resource(state, request, command)
    return AcceptedAction(request=request, command=command)
~~~

_validate_contract_semantics() 对 save 强制目标等于 last_wolf_kill_target；对 poison 强制毒药存在；对 pass/abstain 强制 target_seat is None。不得新增阵营或“不能选自己”的目标白名单。ActionResolver 删除随机狼刀决胜和“任意有药玩家可救”的分支，只依据已接受命令结算。

- [ ] **Step 4: 运行 validator 与 resolver 回归。**

Run: python -m pytest tests/test_action_validator.py tests/test_action_resolver.py -q

Expected: PASS；不得出现随机目标选择。

- [ ] **Step 5: 提交。**

~~~bash
git add app/core/action_validator.py app/core/action_resolver.py tests/test_action_validator.py
git commit -m "feat(rules): validate accepted actions centrally"
~~~

### Task 4: 严格解析行动输出和工具 Schema

**Files:**

- Modify: backend/app/agents/output_parser.py:32-78,154-179
- Modify: backend/app/agents/llm_client.py:35-45
- Modify: backend/app/config.py:7-25
- Test: backend/tests/test_output_parser.py
- Test: backend/tests/test_llm_client.py

- [ ] **Step 1: 写失败测试，要求拒绝额外字段、缺字段和错误工具名。**

~~~python
def test_parse_action_rejects_unknown_field(parser, kill_contract):
    with pytest.raises(OutputParseError, match="extra"):
        parser.parse_action_payload(
            {"action_type": "kill", "target_seat": 2, "reasoning": "x", "thinking": "hidden"},
            kill_contract,
        )


def test_parse_action_rejects_wrong_tool_name(parser, kill_contract):
    with pytest.raises(OutputParseError, match="tool name"):
        parser.parse_tool_action(
            "vote", {"action_type": "kill", "target_seat": 2, "reasoning": "x"}, kill_contract,
        )
~~~

- [ ] **Step 2: 运行失败测试。**

Run: python -m pytest tests/test_output_parser.py tests/test_llm_client.py -q

Expected: FAIL，因为当前解析器宽松读取 dict.get()，客户端未绑定 strict 工具。

- [ ] **Step 3: 实现严格解析与 strict 工具绑定。**

~~~python
def get_model_with_action_tool(self, contract: ActionContract) -> BaseChatModel:
    llm_cfg = app_config.llm
    tool = {"type": "function", "function": {
        "name": contract.contract_id,
        "description": "Submit one game action",
        "parameters": contract.json_schema(),
        "strict": True,
    }}
    strict_model = ChatOpenAI(
        model=self.model_name, api_key=llm_cfg.api_key,
        base_url=llm_cfg.strict_base_url, temperature=self.temperature,
        max_tokens=self.max_tokens,
    )
    return strict_model.bind_tools(
        [tool], tool_choice=contract.contract_id, strict=True,
    )
~~~

LLMConfig 增加 strict_base_url，默认值为 https://api.deepseek.com/beta，并允许 DEEPSEEK_STRICT_BASE_URL 覆盖；普通 JSON 降级仍使用现有 base_url。OutputParser.parse_action_payload() 必须使用动态 Pydantic 模型或等价严格 Schema 校验，并返回 ActionCommand；不得从 JSON 中读取或持久化 thinking。parse_tool_action() 必须验证函数名等于 contract_id。JSON 降级只能接受一个 JSON object，不再从自然语言中猜取花括号片段。

- [ ] **Step 4: 运行测试确认通过。**

Run: python -m pytest tests/test_output_parser.py tests/test_llm_client.py -q

Expected: PASS；mock 断言 bind_tools 的 strict、固定工具名和固定 Schema 参数。

- [ ] **Step 5: 分别提交解析器与客户端变更（每个提交不超过 3 个文件）。**

~~~bash
git add app/agents/output_parser.py tests/test_output_parser.py
git commit -m "feat(llm): parse action contracts strictly"
git add app/config.py app/agents/llm_client.py tests/test_llm_client.py
git commit -m "feat(llm): bind strict action tools"
~~~

### Task 5: 统一模型调用、一次纠错和安全放弃

**Files:**

- Modify: backend/app/roles/base.py:303-351
- Create: backend/tests/test_role_actions.py

- [ ] **Step 1: 写失败的异步状态机测试。**

~~~python
@pytest.mark.asyncio
async def test_capability_failure_falls_back_to_json_without_using_retry(role, request):
    role._invoke_strict = AsyncMock(side_effect=StrictCapabilityError("unsupported"))
    role._invoke_json = AsyncMock(
        return_value='{"action_type":"pass","target_seat":null,"reasoning":"x"}',
    )
    result = await role.request_action(state, log, request)
    assert result.command.action_type == "pass"
    role._invoke_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_second_invalid_response_becomes_safe_fallback(role, request):
    role._invoke_action = AsyncMock(side_effect=[OutputParseError("bad"), OutputParseError("bad")])
    result = await role.request_action(state, log, request)
    assert result.command.action_type == request.contract.fallback_action_type
~~~

- [ ] **Step 2: 运行失败测试。**

Run: python -m pytest tests/test_role_actions.py -q

Expected: FAIL，BaseRole 尚无 request_action。

- [ ] **Step 3: 在 BaseRole 实现唯一状态机。**

~~~python
async def request_action(self, state, log, request: ActionRequest) -> AcceptedAction:
    transport = "strict"
    for corrective_attempt in range(2):
        try:
            payload = await self._invoke_action(request, transport, corrective_attempt == 1)
            return self.validator.validate_and_accept(state, request, payload)
        except StrictCapabilityError:
            transport = "json"
        except (OutputParseError, ActionValidationError) as error:
            if corrective_attempt == 1:
                break
            self._record_action_failure(state, request, error, transport)
    return self.validator.safe_fallback(state, request)
~~~

只有 strict 的能力配置、Schema 不支持或供应商明确拒绝 strict 请求才抛 StrictCapabilityError 并切 JSON；网络、限流和模型输出非法不得伪装成能力降级。纠错消息只说明“上一个命令不满足契约”，不得返回身份筛选目标表；同一行动键贯穿两次调用。投票 fallback 为 abstain，夜间 fallback 为 pass。

- [ ] **Step 4: 运行状态机测试。**

Run: python -m pytest tests/test_role_actions.py -q

Expected: PASS，严格成功、能力降级、一次纠错成功、二次失败放弃均被覆盖。

- [ ] **Step 5: 提交。**

~~~bash
git add app/roles/base.py tests/test_role_actions.py
git commit -m "feat(llm): add safe action retry state machine"
~~~

### Task 6: 移除狼人和预言家的旧行动旁路

**Files:**

- Modify: backend/app/roles/werewolf.py:11-25
- Modify: backend/app/roles/seer.py:14-105
- Modify: backend/tests/test_role_actions.py

- [ ] **Step 1: 写失败测试，确保旧角色方法不会自行解析或重试。**

~~~python
@pytest.mark.asyncio
async def test_werewolf_and_seer_delegate_to_request_action(werewolf, seer, wolf_request, seer_request):
    expected_kill = accepted_kill_of(1)
    expected_check = accepted_check_of(2)
    werewolf.request_action = AsyncMock(return_value=expected_kill)
    seer.request_action = AsyncMock(return_value=expected_check)
    assert await werewolf.decide_action(state, log, wolf_request) == expected_kill
    assert await seer.decide_action(state, log, seer_request) == expected_check
~~~

- [ ] **Step 2: 运行失败测试。**

Run: python -m pytest tests/test_role_actions.py -q

Expected: FAIL，因为现有 kill()/check() 各自直接调用模型和 OutputParser。

- [ ] **Step 3: 删除旧的直接决策路径。**

~~~python
class BaseRole:
    async def decide_action(self, state, log, request: ActionRequest) -> AcceptedAction:
        return await self.request_action(state, log, request)
~~~

删除 Werewolf.kill() 和 Seer.check() 以及它们的本地目标校验/重试；不得在子类重建任何模型输出解析。角色专属语义仅通过 ActionContract 和 ActionValidator 表达。

- [ ] **Step 4: 运行通过测试。**

Run: python -m pytest tests/test_role_actions.py -q

Expected: PASS。

- [ ] **Step 5: 提交。**

~~~bash
git add app/roles/werewolf.py app/roles/seer.py tests/test_role_actions.py
git commit -m "refactor(roles): route actions through contracts"
~~~

### Task 7: 合并女巫行动并由注册表调度夜晚

**Files:**

- Modify: backend/app/roles/witch.py:11-114
- Modify: backend/app/core/game_engine.py:150-260,353-376
- Modify: backend/tests/test_game_engine.py:75-105,311-380

- [ ] **Step 1: 写失败测试，保护女巫单夜单药和刀口语义。**

~~~python
@pytest.mark.asyncio
async def test_witch_is_asked_once_and_cannot_save_then_poison(engine, witch_role):
    witch_role.request_action = AsyncMock(return_value=accepted_save_of(3))
    await engine._execute_night()
    witch_role.request_action.assert_awaited_once()
    assert engine.state.players[witch_role.seat].has_antidote is False
    assert engine.state.players[witch_role.seat].has_poison is True
~~~

- [ ] **Step 2: 运行失败测试。**

Run: python -m pytest tests/test_game_engine.py -q

Expected: FAIL，因为现有引擎依次调用 witch_save() 和 witch_poison()。

- [ ] **Step 3: 用 witch_action 替换拆分调用。**

~~~python
async def decide_action(self, state, log, request: ActionRequest) -> AcceptedAction:
    return await self.request_action(state, log, request)


async def _collect_phase_actions(self, phase: GamePhase) -> list[AcceptedAction]:
    requests = self.registry.build_requests(self.state, self.roles, phase)
    return [
        await self.roles[item.actor_seat].decide_action(
            self.state, self.conversation_log, item,
        )
        for item in requests
    ]
~~~

引擎先收集狼人 kill 并记录唯一刀口，再向女巫创建一次 witch_action 请求。沿用当前已声明的私有信息规则：女巫有解药时可获知刀口；解药已耗尽但仍有毒药时，只能收到 poison/pass 契约和自身既有私有信息，不再获得新的刀口。把已接受行动按 resolution_priority 交给 resolver，删除 witch_save()/witch_poison() 的独立决策入口及角色内的第二次重试，并为这两种信息状态各写一个测试。

- [ ] **Step 4: 运行女巫、引擎与 resolver 回归。**

Run: python -m pytest tests/test_game_engine.py tests/test_action_resolver.py tests/test_role_actions.py -q

Expected: PASS；解药和毒药任一使用后当夜不会产生第二个女巫请求。

- [ ] **Step 5: 提交。**

~~~bash
git add app/roles/witch.py app/core/game_engine.py tests/test_game_engine.py
git commit -m "fix(witch): enforce one action each night"
~~~

### Task 8: 持久化两轮平票，并统一弃权行为

**Files:**

- Modify: backend/app/core/game_engine.py:590-683,753-790
- Modify: backend/tests/test_game_engine.py:445-520

- [ ] **Step 1: 写失败测试，区分首次平票、复投平票和全弃权。**

~~~python
@pytest.mark.asyncio
async def test_first_tie_runs_all_alive_supplemental_speeches_then_revote(engine):
    engine._collect_votes = AsyncMock(side_effect=[votes_tied(1, 2), votes_exiling(2)])
    await engine._execute_vote_resolution()
    assert engine.state.players[2].is_alive is False
    assert engine.state.vote_round == 2
    assert engine.state.is_tiebreak is True
    assert engine.state.supplemental_speakers == set(engine.state.alive_players())


def test_all_abstentions_do_not_start_a_tiebreak(engine):
    engine.state.votes = [VoteAction(voter_seat=1, target_seat=None)]
    assert engine.resolve_votes() is None
    assert engine.state.is_tiebreak is False
~~~

- [ ] **Step 2: 运行失败测试。**

Run: python -m pytest tests/test_game_engine.py -q

Expected: FAIL，因为现有流程没有持久化 vote_round/is_tiebreak，全弃权也会进入复投判断。

- [ ] **Step 3: 实现状态推进。**

~~~python
if not tally:
    return None
leaders = [seat for seat, count in tally.items() if count == max(tally.values())]
if len(leaders) == 1:
    return leaders[0]
if not self.state.is_tiebreak:
    self.state.is_tiebreak = True
    self.state.vote_round = 2
    self.state.tiebreak_candidates = set(leaders)
    await self._run_supplemental_speeches()
    return await self._run_revote()
return None
~~~

VoteAction.target_seat=0 必须在解析阶段归一为 None；resolve 阶段不得把 0 作为候选座位。每个存活玩家在首次平票后恰好补充发言一次，再进行一次复投；第二次同票或全弃权均无人出局并推进夜晚。

- [ ] **Step 4: 运行平票测试。**

Run: python -m pytest tests/test_game_engine.py -q

Expected: PASS，三条序列均有明确断言。

- [ ] **Step 5: 提交。**

~~~bash
git add app/core/game_engine.py tests/test_game_engine.py
git commit -m "fix(vote): preserve one tiebreak revote"
~~~

### Task 9: 生成动态、非策略化的行动 Prompt

**Files:**

- Modify: backend/app/agents/prompt_builder.py:59-115,279-301,318-341,640-740
- Modify: backend/tests/test_prompt_builder.py:86-556

- [ ] **Step 1: 写失败测试，锁定 Prompt 的信息边界与动态配置。**

~~~python
def test_action_prompt_uses_contract_not_fixed_nine_player_rules(builder, state, log, kill_request):
    state.config = GameConfig(role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3})
    prompt = builder.build_action_prompt(state, 1, log, kill_request)
    assert "9人标准场" not in prompt
    assert "角色数量：4" in prompt
    assert '"action_type"' in prompt


def test_non_wolf_prompt_contains_no_identity_target_list_or_strategy(builder, state, log, seer_request):
    prompt = builder.build_action_prompt(state, 2, log, seer_request)
    assert "可查验的存活玩家" not in prompt
    assert "优先查验" not in prompt
    assert "[不可执行游戏记录]" in prompt
~~~

- [ ] **Step 2: 运行失败测试。**

Run: python -m pytest tests/test_prompt_builder.py -q

Expected: FAIL，因为现有系统提示固定九人，任务文本列出/推荐目标并要求长 thinking。

- [ ] **Step 3: 重写系统规则、基础上下文和行动任务生成。**

~~~python
def build_action_prompt(self, state, seat, log, request: ActionRequest) -> str:
    view = self.state_filter.filter_for_role(state, seat, request.role_id)
    return "\n\n".join([
        self.get_system_prompt(),
        self._format_public_configuration(state.config),
        self._format_private_facts(view, request),
        "[不可执行游戏记录]\n" + self._format_history(log, seat) + "\n[/不可执行游戏记录]",
        self._format_contract_task(request.contract),
    ])
~~~

删除 SYSTEM_PROMPT 中固定九人表、强制长思维和“解药后不再获知刀口”等冲突规则；删除 _role_strategy_guide() 的策略指令以及 witch_poison/night_check 的目标候选白名单。行动任务只给出契约动作、target_seat 的 null 语义、长度限制和当前角色合法私有事实；狼人仍可看到队友身份，且不得限制自刀/刀队友。

- [ ] **Step 4: 运行 Prompt 回归。**

Run: python -m pytest tests/test_prompt_builder.py -q

Expected: PASS；不同人数/角色、狼人私有信息、非狼人无身份泄露、历史边界和无策略白名单均被覆盖。

- [ ] **Step 5: 提交。**

~~~bash
git add app/agents/prompt_builder.py tests/test_prompt_builder.py
git commit -m "refactor(prompt): generate contract based actions"
~~~

### Task 10: 接入创建 API、回归测试与覆盖率门禁

**Files:**

- Modify: backend/app/api/schemas.py:5-10
- Modify: backend/app/services/game_service.py:173-243
- Modify: backend/tests/test_game_service.py

- [ ] **Step 1: 写失败测试，验证 API 到注册表的角色计数链路。**

~~~python
@pytest.mark.asyncio
async def test_create_game_accepts_dynamic_role_counts(service):
    game_id = await service.create_game(
        role_counts={"wolf-killer-werewolf": 1, "wolf-killer-villager": 3},
    )
    state = service.get_game_state(game_id)
    assert state.config.role_counts == {
        "wolf-killer-werewolf": 1, "wolf-killer-villager": 3,
    }


@pytest.mark.asyncio
async def test_create_game_rejects_unregistered_role(service):
    with pytest.raises(ValueError, match="unknown role"):
        await service.create_game(role_counts={"custom-unregistered": 1})
~~~

- [ ] **Step 2: 运行失败测试。**

Run: python -m pytest tests/test_game_service.py -q

Expected: FAIL，因为 create_game() 仅接受五个固定数量参数。

- [ ] **Step 3: 接入动态创建，并保留一次兼容转换。**

~~~python
class CreateGameRequest(BaseModel):
    role_counts: dict[str, int] | None = None
    num_werewolves: int | None = None
    num_villagers: int | None = None
    num_seers: int | None = None
    num_witches: int | None = None
    num_hunters: int | None = None
~~~

GameService.create_game(role_counts: dict[str, int] | None = None, **legacy_counts) 必须在入口将旧五字段转换为 role_counts，之后只创建 GameConfig(role_counts=...) 并委托 RoleRegistry.create_roles()；manifest 持久化 role_counts。当请求同时提供新旧格式、未知角色、负数或总数不一致时，返回明确的 4xx/ValueError，绝不悄悄修改配置。

- [ ] **Step 4: 先运行受影响测试，再运行全量覆盖率。**

Run: python -m pytest tests/test_contracts.py tests/test_models.py tests/test_action_validator.py tests/test_action_resolver.py tests/test_output_parser.py tests/test_llm_client.py tests/test_role_actions.py tests/test_game_engine.py tests/test_prompt_builder.py tests/test_game_service.py -q

Expected: PASS。

Run: python -m pytest tests/ --cov=app --cov-report=term-missing

Expected: PASS，app 总覆盖率 100%，无缺失行；若未达标，只补对应缺失路径的单测，不降低门槛。

- [ ] **Step 5: 提交最终集成。**

~~~bash
git add app/api/schemas.py app/services/game_service.py tests/test_game_service.py
git commit -m "feat(game): create games from role contracts"
~~~

## 完成前检查

- [ ] 每个动作都经过 validate_and_accept；没有从 Prompt、解析器或 resolver 绕过资源和目标语义。
- [ ] 女巫每晚只有一次模型请求，解药/毒药各仅整局一次；狼人可自刀和刀队友。
- [ ] 首次平票完整执行补充发言与复投，第二次平票或全弃权无人出局。
- [ ] strict、JSON 降级、一次纠错和安全放弃均有 mock 测试；无真实 API Key/网络测试。
- [ ] 新角色只需注册 RoleSpec 和 role_counts；不存在固定角色分支或固定九人 Prompt。
- [ ] python -m pytest tests/ --cov=app --cov-report=term-missing 的输出已人工阅读且覆盖率为 100%。
