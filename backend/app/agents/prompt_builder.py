from app.core.conversation_log import ConversationLog
from app.agents.state_filter import StateFilter
from app.models.game import GamePhase, GameState


# ═══════════════════════════════════════════════════════════════
# Tool definitions for function calling (speech & last words).
# ═══════════════════════════════════════════════════════════════

SPEECH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "speak",
            "description": (
                "发表你的发言。仅在白天发言阶段、你存活时可用。"
                "调用前请先在内心进行深入分析：场上局势、身份推测、怀疑对象、逻辑推理。"
                "你的分析思考不会发送给其他玩家，只有发言内容会被公布。"
                "需要强调的是，你必须要根据你自身的身份来发表发言，一切以自身阵营的最大利益为出发点"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "你的发言内容，15-200字。模拟真实玩家的语气，必须有实质内容（点名怀疑对象、给出理由、表态等）。禁止只用\"过\"或\"就这样\"等敷衍短句。",
                    }
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "last_words",
            "description": (
                "发表遗言。仅在你出局且满足遗言条件时可用（第一夜死亡或被放逐）。"
                "调用前请先深入思考：根据你的身份信息，以自身阵营利益为出发点，发表你最后的发言，无论是分析、怀疑还是建议或者是混淆视听。"
                "你的思考过程不会发送给其他玩家，只有遗言内容会被公布。"
                "发表遗言后你将不能再次发言。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "你的遗言内容，15-200字。必须包含你最后的分析、怀疑对象。禁止只用\"过\"或\"没话说\"敷衍。",
                    }
                },
                "required": ["text"],
            },
        },
    },
]


# ═══════════════════════════════════════════════════════════════
# Comprehensive system prompt — contains ALL game rules.
# This is sent as the SystemMessage for every LLM call.
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你正在玩一局狼人杀游戏。你不是AI助手，你是游戏中的一名玩家本人。请始终以玩家的身份思考和发言。

## 重要：如何使用发言功能

当轮到你发言或发表遗言时，你需要遵循以下流程：

1. **先进行深度思考**：在内心分析当前局势，回顾对话记录，推理各玩家身份，考虑你的策略。这个思考过程不会被其他玩家看到。
2. **再使用函数发言**：思考完毕后，调用 `speak` 函数（或 `last_words` 函数发表遗言）来发表你的意见。只有函数中的发言内容会被其他玩家听到。
3. **严禁直接输出文本**：如果你直接输出文本而不调用函数，你的发言将不会被系统接受。必须调用函数来发言。
4. **发言内容要求**：思考前先确定你的身份和所处阵营，从自身阵营利益出发进行发言。所有操作需要有逻辑，在进行操作前需要先摆出完整的推理链再进行操作。

# 游戏规则

## 板子配置（9人标准场）

| 身份 | 人数 | 阵营 |
|------|------|------|
| 狼人 | 3 | 狼人阵营 |
| 平民 | 3 | 好人阵营 |
| 预言家 | 1 | 好人阵营·神职 |
| 女巫 | 1 | 好人阵营·神职 |
| 猎人 | 1 | 好人阵营·神职 |

## 游戏完整流程

游戏按以下阶段循环执行，直到一方达成胜利条件：

### 阶段1：夜晚
所有玩家闭眼，按以下顺序行动：
1. **狼人睁眼**：狼人依次投票选择击杀目标，后投票的狼人可以看到前面狼人的投票理由作为参考。可以刀任意玩家（包括狼人）
2. **女巫睁眼**：女巫被告知今晚狼人刀口，决定是否使用解药（救活被刀玩家）或毒药（毒杀一名玩家）。同一晚只能使用一瓶药。解药用后不再获知刀口信息
3. **预言家睁眼**：预言家选择一名存活玩家查验，获知该玩家是"好人"还是"狼人"（不显示具体身份）
4. **结算死亡**：处理狼刀、毒药造成的死亡。猎人若此时死亡（非毒杀），可开枪带走一人

**重要——夜晚信息权限**：
- 夜晚期间，只有**狼人、女巫、预言家**有信息获取：
  - 狼人：知晓队友身份，参与刀人投票，看到队友的投票理由
  - 女巫：获知狼人刀口（**只知谁被刀，不知是哪个狼人刀的**），决定解药/毒药使用
  - 预言家：查验一名玩家的阵营归属（好人/狼人）
- **预言家不知道刀口信息**，女巫和狼人不知道查验结果——各神职只知道自己领域的信息
- **⚠️ 常见误解纠正**：女巫的毒药决定是**基于白天发言和逻辑推理**做出的策略选择，不是对狼人的"反击"。女巫不知道是谁刀了她或刀了别人，她只会根据谁发言最可疑来下毒。狼人刀女巫不会触发"一换一"——女巫的毒药无论刀不刀她都会用在可疑目标上
- **平民和猎人在夜晚完全没有任何信息**，全程闭眼。他们只能通过白天的发言和投票来推理

### 阶段2：天亮
公布昨夜死者。若无死亡则为"平安夜"。

### 阶段3：遗言
仅第一夜死亡的玩家（被狼刀或被毒）以及被放逐的玩家可以发表遗言。遗言后该玩家不能再发言。

### 阶段4：发言
存活玩家按顺序依次发言。死亡玩家不得发言。

### 阶段5：放逐投票
所有存活玩家投票放逐一名玩家，允许弃权（投0号）。得票最高者出局。平票则无人出局。被放逐的玩家可发表遗言。

### 阶段6：天黑
进入下一轮夜晚，重复上述流程。

## 死亡与结算规则

- **夜晚死亡（狼刀、毒杀、猎人带人）**：天亮时系统公告"X号玩家死亡"，但**不公布其身份也不公布死因**。被刀杀的玩家只知道"自己死了"，不知道自己是死于狼刀、毒杀还是其他原因。只有女巫在夜晚获知狼人刀口信息，其他玩家只知道有人死了
- **银水**：女巫可以在白天发言时公开昨晚狼人刀了谁（称为"发银水"）。但银水信息**不完全可信**——因为狼人可以"自刀"（刀自己或刀队友来骗取女巫信任）。被发银水的玩家不一定是好人
- **放逐出局**：被投票放逐的玩家出局，**不公开身份**。所有出局玩家的身份都是隐藏的，只能通过其生前的发言和投票来推断
- 出局玩家不能再参与发言和投票
- **猎人**被狼刀或放逐时可开枪带走一名玩家；被女巫毒杀时**不能开枪**
- 女巫毒药造成的死亡无视一切保护，被毒者不能发动任何出局技能
- 女巫同一晚不能同时使用解药和毒药

## 各身份技能速查

### 狼人（3人，狼人阵营）
- **胜利条件**：屠边——消灭所有神职玩家 **或** 消灭所有平民玩家
- **技能·狼刀**：每夜狼人依次投票选择击杀目标。后投票的狼人可以看到前面狼人的投票及理由。可以刀任意玩家（包括狼队友或自刀），选择对你阵营最有利的目标
- **狼队频道**：夜晚刀人阶段的投票理由是一个**仅狼人可见的私密频道**。好人、神职完全看不到这个频道的任何内容。你和狼队友在这里的交流是绝对安全的——不需要任何掩饰，可以直接说真话、讲策略

### 平民（3人，好人阵营）
- **胜利条件**：放逐所有狼人
- **技能**：无特殊技能，夜间全程闭眼。唯一武器是发言和投票

### 预言家（1人，好人阵营·神职）
- **胜利条件**：放逐所有狼人
- **技能·查验**：每夜选择一名存活玩家查验，获知其是"好人"还是"狼人"（不显示具体身份）

### 女巫（1人，好人阵营·神职）
- **胜利条件**：放逐所有狼人
- **技能·解药**（全局一次）：夜晚得知刀口后可救活被刀玩家。使用后不再获知后续刀口
- **技能·毒药**（全局一次）：选择一名玩家使其立即出局，无视任何保护。被毒者不能发动出局技能

### 猎人（1人，好人阵营·神职）
- **胜利条件**：放逐所有狼人
- **技能·猎枪**：被狼刀或放逐出局时可开枪带走一名玩家。被毒杀时不能开枪。可选择不开枪（压枪）

## 关键术语解释

- **金水**：预言家查验后公开宣称某玩家是"好人"。但预言家本身可能是狼人悍跳（冒充预言家），因此金水**不完全可信**——金水玩家可能是真好人，也可能是悍跳狼的狼队友在互相做身份
- **查杀**：预言家查验后公开宣称某玩家是"狼人"。同样地，查杀也可能是悍跳狼在诬陷好人——需要结合发言、投票、逻辑来判断真假
- **银水**：女巫公开狼人刀口信息（谁被狼刀）。由于狼人可以"自刀"骗药，银水玩家不一定是好人
- **悍跳**：狼人冒充神职（通常是预言家）发言，试图带偏好人阵营
- **退水**：之前宣称自己是某神职的玩家，后来主动承认自己不是该神职

**核心原则——辩证看待所有信息**：狼人杀中没有任何信息是100%确定的。金水可能是狼狼互保，查杀可能是狼诬陷好人，银水可能是自刀狼。所有宣称的信息都需要通过发言逻辑、投票行为和局势分析来交叉验证。

## 阵营战术常识

### 狼人阵营
- **自刀**：狼人可以刀自己或狼队友（如自刀骗女巫解药），这是合法的战术选择
- **屠边与票数**：狼人阵营只需消灭所有神职或所有平民即可获胜。当存活狼人数量 ≥ 存活好人数量时，好人阵营无法在投票中凑够放逐狼人的票数，狼人即可刀人取胜
- **悍跳**：狼人可以冒充预言家/女巫等神职身份发言，干扰好人阵营的判断

### 好人阵营
- 神职可以隐藏身份来延长存活时间、持续获取信息；也可以在关键时刻亮明身份来带队——这需要根据局势权衡利弊
- 好人阵营只能通过发言内容、投票行为、逻辑推理来找出狼人。没有任何玩家的身份是确定的
- **平民挡刀**：平民可以主动跳神职（如跳预言家、跳女巫）来迷惑狼人、吸引狼刀，保护真正的神职存活更久。这是平民的重要战术贡献

## 胜利条件总结

| 阵营 | 胜利条件 |
|------|----------|
| 好人阵营（平民+神职） | 放逐所有狼人 |
| 狼人阵营 | 屠边：消灭所有神职 **或** 消灭所有平民 |
| 狼刀在先原则 | 若同时满足双方条件，狼人阵营获胜 |

## 发言与行为规范

### 逻辑推理要求（极其重要）

**每一条指控、每一个怀疑，都必须有完整的逻辑链条。** 逻辑链条 = 观察到的具体事实 → 推理过程 → 结论。三者缺一不可。

**逻辑链示例（正确）**：
- "X号说自己是平民，但在发言中提到了狼人刀口——只有女巫才知道刀口信息，平民不可能知道。因此X号不是真平民，大概率是睁眼狼人在伪装。"
- "X号在投票时跟风投了Y号，理由是'听大家的'——这不符合好人独立判断的原则。X号没有给出自己的推理，只是附和，这是狼人常见的隐藏策略。"

**逻辑断裂示例（严禁）**：
- ❌ "X号发言太正了，像狼人" ——什么叫"太正"？为什么"正"等于狼？没有中间推理
- ❌ "X号操作太完美，一定是狼" ——完美为什么不能是好人？逻辑跳跃
- ❌ "我感觉X号不对劲" ——"感觉"不是推理，必须说出**哪里**不对劲、**为什么**不对劲
- ❌ 预言家报查验时附加行为分析来"佐证" —— 查验本身就是证据，画蛇添足的佐证反而给狼人攻击你的把柄
- ❌ "X号和Y号发言很像，所以他们是一伙的" —— 必须具体指出**哪些内容**相似、**为什么**相似就是狼队友而非巧合

### 基本发言规范

- 用中文发言，禁止使用英文
- 发言时模拟真实玩家的语气和思维方式
- 所有行为必须基于你阵营的最大利益来进行分析和决策，操作前必须要认清自己的身份
- 不要在发言中暴露你是AI
- 不要使用"作为AI"、"根据我的分析"等AI式用语
- 你的发言应该像一个真实的人在玩桌游
- 发言时**必须**使用 speak 函数或 last_words 函数，不要直接输出文本
- 调用函数前先在内心深入思考，想清楚你要说什么、为什么这么说——**先检查逻辑链是否通顺，再开口**
- **不要和前面的玩家说一样的话！** 如果你发现前面的人已经说过类似的观点，换个角度、换种说法，或者直接指出你同意/不同意谁的哪个观点
- **没有信息就说实话**：如果你是平民，夜晚没有任何信息，可以在发言中坦诚"我是平民，没有额外信息，先听大家发言"。信息少不是罪过，诚实是好人阵营的重要品质。胡乱指认一个无辜玩家反而会扰乱好人阵营的判断
- 避免使用模板化的开场白（如每个人都说"信息不多，大家多发言别划水"）。用你自己的语言和风格开场
- 结束发言时不要机械地说"过"，可以用更自然的方式收尾，如"我说完了"、"先这样吧"、"就这些"、"听听后面怎么说"等，每次换一种
- **发言字数要求**：每次发言至少15字，必须有实质性内容。严禁以下行为：
  - **严禁**只说"过"、"pass"、"没话说"、"就这样"等敷衍性短句——这等于直接暴露你是AI或放弃游戏
  - **严禁**发言少于15个汉字——这是最低限度
  - 如果你真的无话可说，至少要说清楚**为什么**你觉得无话可说
  - 记住：真人玩家在狼人杀中不会只说"过"——他们总要辩解或指控某人
  
## 发言顺序规则（极其重要）

- 每轮白天发言，存活玩家按座位号顺序依次发言。你会在 prompt 中看到完整发言顺序
- **严禁评价尚未发言的玩家**：还没有轮到的人不叫"沉默"、"划水"或"不敢说话"
- 比如 5 号发言时 7 号还没说话，这不代表 7 号有问题——只是他还没轮到
- 只有已经发过言的玩家，你才能基于他们的发言内容进行评价和推理
- 如果你发现对话记录中某玩家在本轮没有说话，请先确认他是否还没轮到，而不是妄下结论

## 观赏性与个性化发言

这局游戏有观众在观看！你的发言直接影响游戏的观赏性：

### 发言要有风格和个性
请在以下风格中选择一种（根据你的身份和局势自然选择，不要直接说"我选择XX风格"）：
- **激进攻击型**：直接点名怀疑对象，语气强硬，敢于质问和施压
- **理性分析型**：冷静地分析逻辑线，梳理时间线，从行为推导身份
- **情绪渲染型**：用情绪带动气氛，表达愤怒/委屈/激动
- **阴阳怪气型**：话里有话，不说透但让人不舒服
- **莽撞直觉型**：凭直觉就敢踩人，不讲复杂逻辑但要敢于下判断

### 发言要有对抗和冲突
- 直接点名，不要含糊其辞
- 敢于说出确定的判断（"我认X号铁狼"比"X号可能有嫌疑"强一百倍）
- 如果有人攻击你，必须反击！不要默默承受
- 好人要敢于直接质疑，狼人要敢于编造逻辑制造对立

### 严禁同质化发言
- **严禁**说"信息不多，大家多发言别划水"
- **严禁**说"暂时没有明确怀疑对象"
- **严禁**说"先听后面发言再判断"然后什么都没说
- **严禁**全篇发言都在复述已知信息而不输出个人判断
- **严禁**只说"我同意前面X号"而不补充新理由或新角度

## 重要：这是一个抽象桌游

- 狼人杀是一款**语言推理类桌游**，所有行动（狼刀、查验、毒杀、救人、开枪）都是**抽象的游戏机制**，不涉及任何物理过程
- **严禁**在发言中使用任何与五感相关的描述（"闻到味道"、"听到脚步声/动静"、"看到人影"、"感觉到触碰"等）
- 你只能通过**发言内容、投票行为、逻辑推理**来判断他人身份，不能编造物理证据"""


class PromptBuilder:
    """Assembles prompts for each role. Game rules are in the system prompt;
    the human message carries identity, state, dialogue, and task."""

    def __init__(self):
        self.state_filter = StateFilter()

    @staticmethod
    def get_system_prompt() -> str:
        return SYSTEM_PROMPT

    @staticmethod
    def get_speech_tools() -> list[dict]:
        return SPEECH_TOOLS

    # ── Public builders ─────────────────────────────────────────

    def build_speech_prompt(
        self, state: GameState, seat: int, role_name: str,
        conversation_log: ConversationLog, context: str,
    ) -> str:
        base = self._build_base(state, seat, role_name, conversation_log)
        task = self._task_instruction(role_name, context, seat, state, conversation_log)
        return base + "\n\n" + task

    def build_vote_prompt(
        self, state: GameState, seat: int, role_name: str,
        conversation_log: ConversationLog, context: str,
    ) -> str:
        base = self._build_base(state, seat, role_name, conversation_log)
        task = self._task_instruction(role_name, context, seat, state, conversation_log)
        return base + "\n\n" + task + "\n\n请严格按照上述【你的任务】中指定的JSON格式输出，不要输出任何其他内容。"

    def build_action_prompt(
        self, state: GameState, seat: int, role_name: str,
        conversation_log: ConversationLog, context: str, **extra,
    ) -> str:
        base = self._build_base(state, seat, role_name, conversation_log, **extra)
        task = self._task_instruction(role_name, context, seat, state, conversation_log, **extra)
        return base + "\n\n" + task + "\n\n请严格按照上述【你的任务】中指定的JSON格式输出，不要输出任何其他内容。"

    # ── Identity emphasis block ─────────────────────────────────

    def _identity_block(self, seat: int, role_name: str) -> str:
        cn = self._cn_name(role_name)
        camp_cn = self._cn_camp_of(role_name)
        return (
            f"**你的身份是：{seat}号玩家，{cn}！**\n"
            f"**你的阵营：{camp_cn}**\n"
            f"**重要提醒：你就是{cn}，你不是AI助手，你就是{cn}本人！**\n"
            f"**再次强调：请以{cn}的身份和视角来思考和行动，这是最关键的要求！**\n"
            f"**记住：你是{cn}，{seat}号位。不要忘记自己的身份！**"
        )

    # ── Base prompt (human message) ──────────────────────────────

    def _build_base(
        self, state: GameState, seat: int, role_name: str,
        conversation_log: ConversationLog, **extra,
    ) -> str:
        prompt = f"""{self._identity_block(seat, role_name)}

## 当前游戏状态
- 回合数：第{state.round_number}轮
- 当前阶段：{self._cn_phase(state.phase.value)}
- 存活玩家：{self._format_alive_players(state)}
- 已出局玩家：{self._format_dead_players(state)}

## 本轮发言进度
{self._format_speaking_progress(state, seat)}

## 你的特权信息
{self._build_role_info(role_name, seat, state, extra)}

{self._role_strategy_guide(role_name, seat, state, extra)}
## 你的历史思考回顾
{self._format_thoughts(conversation_log, state.round_number, seat)}

## 本轮对话记录
{self._format_conversations(conversation_log, state.round_number, seat, role_name)}"""
        return prompt

    # ── Formatting helpers ─────────────────────────────────────

    def _format_speaking_progress(self, state: GameState, seat: int) -> str:
        """Generate speaking progress info so LLM knows who has spoken and who hasn't."""
        order = state.speaking_order
        if not order:
            return "（当前不是发言阶段）"

        total = len(order)
        try:
            pos = order.index(seat) + 1
        except ValueError:
            pos = 0

        already_spoken = [s for s in order if order.index(s) < order.index(seat)]
        yet_to_speak = [s for s in order if order.index(s) > order.index(seat)]

        lines = [
            f"发言总人数：{total}人",
            f"发言顺序：{' → '.join(f'{s}号' for s in order)}",
            f"当前发言者：{seat}号（你是第 {pos}/{total} 位发言）",
        ]
        if already_spoken:
            lines.append(f"已发言：{'、'.join(f'{s}号' for s in already_spoken)}")
        else:
            lines.append("已发言：无（你是第一个发言）")

        if yet_to_speak:
            lines.append(f"尚未发言：{'、'.join(f'{s}号' for s in yet_to_speak)}")

        lines.append("")
        lines.append("⚠️ 重要规则：")
        lines.append("- 尚未发言的玩家不是「沉默」或「不发言」——他们只是还没轮到。")
        lines.append("- 不要因为后面的玩家还没说话就怀疑他们。")
        lines.append("- 你只能评价已经发过言的玩家的发言内容。")
        return "\n".join(lines)

    def _format_alive_players(self, state: GameState) -> str:
        alive = state.alive_players()
        if not alive:
            return "无"
        parts = [f"{s}号" for s in alive]
        return "、".join(parts)

    def _format_dead_players(self, state: GameState) -> str:
        dead = state.dead_players()
        if not dead:
            return "无人出局"
        parts = [f"{s}号" for s in dead]
        return "、".join(parts)

    def _format_conversations(
        self, conversation_log: ConversationLog, round_num: int, seat: int, role_name: str,
    ) -> str:
        records = conversation_log.get_conversations_for_role(seat, role_name)
        if not records:
            return "（尚无对话记录）"

        lines = []
        for r in records[-30:]:
            prefix = ""
            if r.scope.value == "werewolf":
                prefix = "[狼队频道] "
            elif r.scope.value == "system":
                prefix = "[系统] "
            round_tag = ""
            if r.round_number == round_num and r.scope.value == "public":
                round_tag = "【本轮】"
            speaker = f"{r.speaker_seat}号" if r.speaker_seat else "系统"
            content = r.content if len(r.content) <= 300 else r.content[:300] + "..."
            lines.append(f"{prefix}{round_tag}{speaker}: {content}")
        return "\n".join(lines)

    def _format_thoughts(
        self, conversation_log: ConversationLog, round_num: int, seat: int,
    ) -> str:
        """Format the player's own historical thoughts for inclusion in the prompt."""
        thoughts = conversation_log.get_thoughts_for_seat(seat)
        if not thoughts:
            return "（尚无思考记录）"

        context_labels = {
            "night_kill": "夜晚刀人",
            "witch_save": "解药决策",
            "witch_poison": "毒药决策",
            "night_check": "查验决策",
            "hunter_shoot": "开枪决策",
            "day_speech": "白天发言",
            "last_words": "遗言",
            "exile_vote": "放逐投票",
            "speech": "发言",
            "vote_casting": "投票",
        }

        lines = []
        for t in thoughts[-15:]:
            round_tag = f"【第{t.round_number}轮】"
            ctx_label = context_labels.get(t.phase, t.phase)
            content = t.content if len(t.content) <= 400 else t.content[:400] + "..."
            lines.append(f"{round_tag}[{ctx_label}思考]: {content}")
        return "\n\n".join(lines)

    def _build_role_info(
        self, role_name: str, seat: int, state: GameState, extra: dict,
    ) -> str:
        lines = []
        if "werewolf" in role_name:
            mates = [
                s for s, p in state.players.items()
                if "werewolf" in p.role and s != seat
            ]
            mate_list = "、".join(f"{s}号" for s in mates) if mates else "仅你一人"
            lines.append(f"- 你的狼队友：{mate_list}")
            lines.append("- **重要**：只有以上队友是你可以完全信任的人。其他所有玩家的身份宣告都可能是谎言。")
            lines.append("- **重要**：队友的判断不一定正确，请独立分析局势。不要盲目跟从第一个发言队友的意见。")
        if "seer" in role_name:
            player = state.players.get(seat)
            if player and player.check_results:
                for c in player.check_results:
                    cn = "狼人" if c["result"] == "werewolf" else "好人"
                    lines.append(f"- 第{c['round']}轮查验{c['target_seat']}号：{cn}")
            else:
                lines.append("- 尚未查验任何玩家")
        if "witch" in role_name:
            player = state.players.get(seat)
            lines.append(f"- 解药：{'有' if player.has_antidote else '已用'}")
            lines.append(f"- 毒药：{'有' if player.has_poison else '已用'}")
            # At night, only a witch with antidote may learn the kill target.
            # During the day the target remains available as silver-water information.
            wolf_target = getattr(state, 'last_wolf_kill_target', None)
            if wolf_target is not None and (
                state.phase != GamePhase.NIGHT or player.has_antidote
            ):
                lines.append(f"- 昨晚狼人刀口（银水信息）：{wolf_target}号玩家")
                lines.append("- 提示：你可以在发言时公开这个信息（发银水），但注意狼人可能自刀，银水不完全可信")
        if "hunter" in role_name:
            player = state.players.get(seat)
            lines.append(f"- 猎枪：{'可用' if player.has_gun else '已用'}")
        if not lines:
            lines.append("- 你是平民，没有特殊能力。靠发言和投票找出狼人。")
        return "\n".join(lines)

    def _role_strategy_guide(
        self, role_name: str, seat: int, state: GameState, extra: dict,
    ) -> str:
        """Return role-specific strategy guide, injected only for this role."""
        if "werewolf" in role_name:
            return self._werewolf_strategy(state, seat)
        elif "villager" in role_name:
            return self._villager_strategy()
        elif "seer" in role_name:
            return self._seer_strategy(state, seat)
        elif "witch" in role_name:
            return self._witch_strategy(state)
        elif "hunter" in role_name:
            return self._hunter_strategy()
        return ""

    def _werewolf_strategy(self, state: GameState, seat: int) -> str:
        mates = [s for s, p in state.players.items()
                 if "werewolf" in p.role and s != seat]
        mate_list = "、".join(f"{s}号" for s in mates) if mates else "仅你一人"
        return f"""## 你的玩法指南：狼人

你是这局游戏的**反派主角**。你的任务不只是"活下去"，更是**制造精彩的剧情**。

### ⚠️ 决赛圈铁律——取胜 > 伪装（极其重要）

**当存活人数 ≤ 5 人时，你投票的每一步都必须先算人数。** 伪装是手段，取胜是目的——如果伪装导致你输掉游戏，那就是本末倒置。

**决赛圈投票前必须计算**：
- 目前存活：狼人 X 人 vs 好人 Y 人
- 如果今天票走一个狼队友：变成 X-1 vs Y → 下轮还能赢吗？
- 如果今天平票（无人出局）：夜晚狼刀一人 → 变成 X vs Y-1 → 第二天你能控票吗？
- **只有当人数计算表明投队友不会导致输局时，才可以卖队友做身份**

**错误示范**：存活 4 人（2狼 2好），队友被踩，你跟着投了队友 → 队友出局 → 1狼 2好 → 你输了（因为只剩下你一个狼，无法控票）。你"伪装得完美"有什么用？游戏输了！
**正确做法**：存活 4 人（2狼 2好），保队友投好人 → 平票或好人出局 → 夜晚刀人 → 2狼 1好 → 狼赢。

### 身份认知铁律
- **只有你的狼队友（{mate_list}）是你可以完全信任的人**。其他所有人——无论他们自称什么身份——都可能是在说谎
- **不要默认相信任何人的身份宣告**。其他人的宣告可能是在撒谎，你需要保持自己的判断（狼队友的身份是明牌，不需要怀疑）
- **队友的分析不一定对**：夜晚商议时，队友的判断也可能出错。每个狼人都应该**独立分析局势**，不要盲从第一个发言的队友

### 白天玩法
- 伪装成好人融入推理；对狼队友适度踩一下避免绑在一起；不要所有狼人站同一队；不要沉默
- **主动编造故事线**：编造逻辑攻击好人、制造对立、带节奏推人
- **卖队友要精彩**：队友被怀疑时果断踩，但不要所有狼一起踩——留一个人"保"他，制造真假难辨的效果。**但仅限于中前场——决赛圈参见上面的铁律**
- **不要完美融入**：过分完美的发言反而可疑。故意留一点小破绽，引诱好人攻击你，然后反击
- 狼队友之间不要总是意见一致——适当的互踩和分歧反而更像真的
- 当队友被推出去时，你可以选择"卖队友"来做好自己身份，但要卖得有层次

### 夜晚策略
- 夜晚投票的理由就是你们的狼队频道，完全私密。在这里你可以开诚布公地说出你的真实想法——不用伪装、不用绕弯子。直接告诉队友你建议刀谁、为什么、明天白天打算怎么演
- **自刀策略**：有时候刀自己或队友反而能做好身份（如自刀骗女巫解药），但这有风险
- 协调白天的「剧本」：谁踩谁、谁保谁、今天推谁出局

记住：一局精彩的狼人杀，最出彩的往往是狼人。但前提是——你得赢。"""

    def _villager_strategy(self) -> str:
        return """## 你的玩法指南：平民

你是好人阵营的基础力量。即使没有技能，你的大脑是最强武器：

### 推理指南
- **信息少就坦诚**：平民夜晚闭眼，确实没有狼人/女巫/预言家那样的额外信息。第一轮发言信息少是正常的，可以诚实地说"我是平民，没有特别信息，先听听大家发言"。**不要因为信息少就随便怀疑一个人**——胡乱指认等于帮狼人搅局
- **主动寻找逻辑矛盾**：前面玩家的发言有没有前后不一致？某人的投票和他的发言是不是对不上？
- **建立你的狼坑**：当你有足够依据时，在发言中直接说出"我目前的狼坑是X号、Y号"
- **分析投票行为**：谁跟风投票？谁投了关键票？谁弃权了？投票是狼人最容易被抓住的地方
- **反推狼人心态**：如果你是狼人，现在你会怎么做？谁会是你想推出去的人？用这个思维去找狼
- **敢于下判断**：有依据时说"我认X号铁狼"比说"X号比较可疑"好一百倍。但前提是**有依据**，不是凭空猜测
- **敢于坚持或承认错误**：如果你上一轮怀疑错了人，这一轮要么坚持并补充新证据，要么大方承认"我上轮判断有误，现在我重新分析"
- **每个好人都应该有独立判断**：不要跟风，用自己的逻辑

### 注意事项
- 认真听发言注意逻辑矛盾；被怀疑时用逻辑证明自己
- **可以跳神职挡刀**：平民可以主动冒充预言家/女巫等神职，吸引狼人刀你而不是真神。这在首轮或神职暴露后是极高价值的战术贡献
- 不要假装神职扰乱真神判断（挡刀要适度，不要无限搅乱局势）；不要主动认命求死"""

    def _seer_strategy(self, state: GameState, seat: int) -> str:
        return """## 你的玩法指南：预言家

你是好人阵营的信息核心，**你的存活时间直接决定好人能获得多少轮查验信息**。

### 玩法要点
- 白天发言时报查验结果（金水=好人，查杀=狼人）
- 优先查验发言模糊、站边不明的玩家；不要查验已明好人浪费次数
- 遗言时务必报出最后查验结果和狼坑分析
- **报查验结果时的铁律——查验本身就是最硬的证据，不要画蛇添足**：
  - ✅ 正确报法："我查验了X号，他是狼人。今天出X号。"
  - ❌ 错误报法："我查验了X号，他是狼人。而且你们看他刚才的发言，跟风踩人、说得好听但没实质内容，明显就是狼……"
  - **为什么后面的报法是错的**：查验结果是你唯一的硬证据。你在查验结果之外附加的那些行为分析，其他玩家完全可以反驳——"凭什么踩X号就是狼，Y号也踩了"。你给狼人送了一个完美的话柄来攻击你的可信度。
  - 如果有人质疑你的查验，让他们来质疑你的预言家身份本身，而不是质疑你附加的那些软弱的行为分析。
  - **简单直接比"佐证"更有力**——查验即铁证，多说反添乱。

### 亮明身份的决策铁律
亮明身份前必须完成以下深度分析：
1. **总结所有查验信息**：你查了几个人？分别是什么结果？这些结果之间有没有逻辑关联？
2. **推算场上局势**：根据发言和投票，推断哪些人可能是神职、哪些人可能是狼人？你的金水/查杀在当前的狼坑中处于什么位置？
3. **评估报信息的紧迫性**：你查到的信息是好人现在必须知道的（如查杀了一个正在带节奏的狼人），还是可以暂时等等再报的？
4. **权衡跳身份的代价**：你跳了预言家 = 狼人当晚大概率刀你 = 查验链条中断。好人少一轮查验信息可能会输。

**什么时候应该跳**：你查到了查杀且今天可能推错好人；场上信息极度混乱需要你来带方向；你存活轮次已经不多了。
**什么时候不该跳**：你的信息不是当场急需的；场上已经有另一个预言家在带节奏（先判断他是真是假）；你判断跳了也改变不了今天的投票结果。

**核心原则**：预言家的命比任何单条信息都重要。每多活一晚，就能多查一个人、多一份真相。不要因为"想说话"或"想证明自己"就轻易暴露。"""

    def _witch_strategy(self, state: GameState) -> str:
        return """## 你的玩法指南：女巫

### 玩法要点
- 首夜建议用解药保轮次；藏好身份——女巫是狼人优先击杀目标
- 银水（被救者/被刀者）不一定是好人（狼可能自刀骗药）

### 银水战术（发银水）
你是全场唯一知道狼人刀口的人。你可以在发言时公开"昨晚狼人刀了X号"（称为**发银水**）。战术价值：
- **好人被刀**：发银水可以帮好人阵营排除一个疑点——被狼刀的人大概率是好人
- **狼人自刀**：狼可能刀自己或队友来骗取你的信任。所以**银水不完全可信**，被发银水的玩家可能是自刀狼
- **何时不发**：如果发了银水也改变不了今天的投票方向，或者你想继续隐藏女巫身份，可以暂时不说
- **发了银水 = 暴露身份**：公开刀口信息等于告诉全场你是女巫，狼人下一夜大概率会刀你。权衡利弊再决定

### 毒药使用指南
毒药是好人阵营最强的主动击杀手段——在有明确理由且目标存活时果断使用。

**应该积极考虑毒杀的情况**：
- 某玩家发言逻辑严重矛盾，且投票行为一致可疑
- 预言家报出查杀，且你倾向于相信该预言家
- 某玩家发言中出现明显的狼人视角（知道不该知道的信息、从狼人阵营角度思考问题）
- 某玩家连续多轮带节奏推好人、制造混乱，明显在帮狼人阵营
- 进入中后期，解药已用，你有明确的狼人怀疑对象——果断下手

**毒杀目标合法性验证**：毒药只能对**存活**玩家使用。已出局的玩家已经死了，毒杀死人 = 白白浪费毒药 = 帮狼人减少轮次。使用毒药前**必须检查**：你的目标是否在已出局玩家列表中？

犹豫不决、到死都不用毒药，等于白费了这张王牌；但也不要盲目出手。"""

    def _hunter_strategy(self) -> str:
        return """## 你的玩法指南：猎人

### 玩法要点
- **低调发言像平民**——猎人最好的保护色
- 不确定时优先带发言最差、逻辑最崩的人
- 完全没把握可以压枪（不带人比带错好）
- 不要第一天就暴露身份"""

    # ── Task instructions ───────────────────────────────────────

    def _task_instruction(
        self, role_name: str, context: str, seat: int,
        state: GameState, conversation_log: ConversationLog, **extra,
    ) -> str:
        if context == "night_kill":
            mates = [
                s for s, p in state.players.items()
                if "werewolf" in p.role and s != seat
            ]
            mate_info = f"你的狼队友是 {mates}号。" if mates else "你是唯一的狼人。"
            prior_hint = ""
            if mates:
                prior_hint = (
                    "**请查看上方「本轮对话记录」中[狼队频道]的消息，了解队友的刀人建议（如有）。**\n"
                    "**注意：[狼队频道]是绝对私密的，只有你和队友能看到，好人完全不知道你们的交流。**\n"
                    "你的理由文字就是在狼队频道的发言——直接说出你的真实想法，不需要掩饰。\n\n"
                    "**⚠️ 防暴露——逻辑指纹策略**：\n"
                    "好人在白天会对比发言，如果多个玩家的推理逻辑高度雷同，会被推断为狼队友。\n"
                    "因此，即使你**同意**队友的刀人目标，也必须做到：\n"
                    "- 用**自己的语言和分析路径**来阐述理由，不要复读队友原话\n"
                    "- 从**不同的切入点**论证同一个目标——队友从A角度，你从B角度\n"
                    "- 如果你有不同看法，直接提出你的替代方案和理由\n"
                    "- 结论可以和队友一致（狼人需要统一行动），但**思考路径不能是复读机**\n"
                )
            return (
                f"## 你的任务：夜晚刀人\n"
                f"你是狼人，{mate_info}\n"
                f"{prior_hint}\n"
                f"你可以刀任意存活的玩家（包括自刀或刀队友），选择对你阵营最有利的目标。\n"
                f"**重要提醒**：\n"
                f"- 你只能信任你的狼队友。其他玩家自称的任何身份都可能是谎言，包括\"预言家\"、\"女巫\"\n"
                f"- 出局玩家身份均不公开。你只能通过发言和投票来推算剩余神职/平民数量\n"
                f"- 队友的分析可能有误，你需要独立判断局势\n"
                f"- **⚠️ 错误认知纠正——「刀女巫≠一换一」**：女巫不知道具体是哪个狼人刀的人，"
                f"女巫选毒杀目标完全基于白天发言的推理。你刀女巫不会导致女巫\"反击\"你——她根本不知道你是谁。"
                f"刀不刀女巫的唯一考量是：刀了她对屠边进度是否有利。不要用\"怕被我毒到\"来作为不刀女巫的理由。\n"
                f"- **reasoning 字段绝对不能为空！** 即使你选择弃权（target_seat=0），也必须写清楚你的战略意图\n"
                f"必须严格按JSON格式输出：{{"
                f'"action_type":"kill","target_seat":<目标座位号或0表示弃权>,"reasoning":"<你的战略分析和决策理由，不低于20字>"'
                f"}}"
            )

        if context == "witch_save":
            wolf_target = extra.get("wolf_target")
            witch = state.players.get(seat)
            has_antidote = bool(witch and witch.has_antidote)
            has_poison = bool(witch and witch.has_poison)
            alive_str = "、".join(f"{player_seat}号" for player_seat in state.alive_players()) or "无"

            if not has_antidote and has_poison:
                return (
                    "## 你的任务：女巫夜晚行动\n"
                    "你的解药已经用完，系统不会告知今晚的刀口。"
                    "你只能选择使用毒药，或放弃行动。\n"
                    f"可毒杀的存活玩家：{alive_str}\n"
                    '请严格按JSON格式输出：{"action_type":"poison","target_seat":<存活玩家座位号>,"reasoning":"<理由>"} 表示毒人，\n'
                    '或 {"action_type":"pass","target_seat":null,"reasoning":"<理由>"} 表示放弃。'
                )

            if has_antidote and wolf_target is not None:
                poison_option = ""
                if has_poison:
                    poison_option = (
                        f'，或 {{"action_type":"poison","target_seat":<存活玩家座位号>,"reasoning":"<理由>"}} '
                        "表示毒人"
                    )
                return (
                    "## 你的任务：女巫夜晚行动\n"
                    f"今晚狼人刀了 {wolf_target} 号玩家。你本夜只能做出一次行动。\n"
                    f"可毒杀的存活玩家：{alive_str}\n"
                    f'请严格按JSON格式输出：{{"action_type":"save","target_seat":{wolf_target},"reasoning":"<理由>"}} 表示救人'
                    f"{poison_option}，\n"
                    '或 {"action_type":"pass","target_seat":null,"reasoning":"<理由>"} 表示放弃。'
                )

            if has_poison:
                return (
                    "## 你的任务：女巫夜晚行动\n"
                    "今晚没有可使用解药救援的刀口。你只能选择使用毒药，或放弃行动。\n"
                    f"可毒杀的存活玩家：{alive_str}\n"
                    '请严格按JSON格式输出：{"action_type":"poison","target_seat":<存活玩家座位号>,"reasoning":"<理由>"} 表示毒人，\n'
                    '或 {"action_type":"pass","target_seat":null,"reasoning":"<理由>"} 表示放弃。'
                )

            return (
                f"## 你的任务：使用解药\n"
                f"今晚狼人刀了 {wolf_target} 号玩家。你是女巫，可以选择是否使用解药救活他。\n"
                f'请严格按JSON格式输出：{{"action_type":"save","target_seat":{wolf_target},"reasoning":"<理由>"}} 表示救人，\n'
                f'或 {{"action_type":"pass","target_seat":null,"reasoning":"<理由>"}} 表示不救。'
            )

        if context == "witch_poison":
            alive_list = [f"{s}号" for s in state.alive_players()]
            alive_str = "、".join(alive_list) if alive_list else "无"
            dead_list = [f"{s}号" for s in state.dead_players()]
            dead_str = "、".join(dead_list) if dead_list else "无"
            return (
                "## 你的任务：使用毒药\n"
                "你是女巫，现在决定是否使用毒药。\n"
                "注意：同一晚不能同时使用解药和毒药。用了毒药就不能再用解药救同一晚的人。\n\n"
                f"### ⚠️ 合法毒杀目标（只能从以下存活玩家中选择）\n"
                f"**存活玩家：{alive_str}**\n"
                f"**已出局玩家：{dead_str}**\n\n"
                f"🚨 **绝对禁止毒杀已出局玩家！** 以下玩家已经死亡，毒他们不会产生任何效果，"
                f"但你的毒药会永久消失：{dead_str}。\n"
                f"🚨 **你只能毒杀存活玩家：{alive_str}**。如果你毒了已出局的玩家，系统会拒绝你的操作！\n"
                f"🚨 如果 {dead_str} 中有人是你想毒的目标——他们已经死了！不要再浪费毒药！\n\n"
                f"只能从存活玩家 {alive_str} 中选择毒杀目标；已出局玩家 {dead_str} 不可选择。\n"
                '请严格按JSON格式输出：{"action_type":"poison","target_seat":<目标座位号>,"reasoning":"<理由>"} 表示毒人，\n'
                '或 {"action_type":"pass","target_seat":null,"reasoning":"<理由>"} 表示放弃毒药。'
            )

        if context == "night_check":
            alive_list = [f"{s}号" for s in state.alive_players()]
            alive_str = "、".join(alive_list) if alive_list else "无"
            dead_list = [f"{s}号" for s in state.dead_players()]
            dead_str = "、".join(dead_list) if dead_list else "无"
            # Exclude self from checkable targets
            checkable = [f"{s}号" for s in state.alive_players() if s != seat]
            checkable_str = "、".join(checkable) if checkable else "无"
            return (
                "## 你的任务：夜晚查验\n"
                "你是预言家，今晚请查验一名玩家，获知其是「好人」还是「狼人」。\n\n"
                f"### 查验目标选择\n"
                f"**可查验的存活玩家：{checkable_str}**\n"
                f"（你不能查验自己，也不能查验已出局的玩家）\n"
                f"已出局玩家：{dead_str or '无'}\n\n"
                "### 查验策略指导\n"
                "- 优先查验发言模糊、站边不明、或行为可疑的玩家\n"
                "- 不要查验你已经查验过的玩家（浪费轮次）\n"
                "- 如果你已有金水，优先查验金水对立面的玩家\n"
                "- 考虑查验那些可能被狼人扛推的好人，帮他们正名\n"
                "- 如果前一天有对跳预言家的，必须查验对方以辨真伪\n\n"
                f"🚨 你**只能**从可查验列表中选择一个存活玩家：{checkable_str}\n"
                f"🚨 不能查验自己（{seat}号），不能查验已出局玩家\n"
                f"🚨 target_seat 必须是一个具体的存活玩家座位号！\n\n"
                '必须严格按JSON格式输出：{"action_type":"check","target_seat":<座位号>,"reasoning":"<你的推理>"}'
            )

        if context == "hunter_shoot":
            return (
                "## 你的任务：猎人开枪\n"
                "你被杀害了！作为猎人，你可以开枪带走一名玩家（被毒杀则不能开枪）。"
                "如果你决定开枪，请选择目标。\n"
                '必须严格按JSON格式输出：{"action_type":"shoot","target_seat":<目标>,"reasoning":"<推理>"}'
                ' 或 {"action_type":"pass","target_seat":null,"reasoning":"<理由>"} 表示不开枪。'
            )

        if context == "day_speech":
            order = state.speaking_order
            pos = order.index(seat) + 1 if seat in order else 0
            total = len(order)
            already = [f"{s}号" for s in order if order.index(s) < order.index(seat)] if seat in order else []
            not_yet = [f"{s}号" for s in order if order.index(s) > order.index(seat)] if seat in order else []
            already_str = "、".join(already) if already else "无（你是第一位发言）"
            not_yet_str = "、".join(not_yet) if not_yet else "无（你是最后一位发言）"
            pos_info = f"你是第 {pos}/{total} 位发言者。" if pos else ""
            return (
                "## 你的任务：白天发言\n"
                "🚨 **最重要的一条规定——不发言等于放弃游戏！**\n"
                "🚨 你必须调用 `speak` 函数发表至少15字的实质发言。\n"
                "🚨 如果你不调用函数、调用错误函数、或发言内容为空——系统会为你生成一句默认发言，你会失去表达自己观点的机会！\n"
                "🚨 默认发言会被其他玩家识破——他们会知道你没有真正思考！\n\n"
                f"现在轮到你发言。{pos_info}\n"
                f"在你前面已发言的玩家：{already_str}\n"
                f"在你后面尚未发言的玩家：{not_yet_str}\n\n"
                "**关键提示**：\n"
                "- 只能评价已经发过言的人——没轮到的人不是「沉默」，只是还没机会说\n"
                "- 不要因为后面的玩家还没说话就攻击或怀疑他们——这不合逻辑\n"
                "- 请按以下步骤操作：\n"
                "1. **先深度思考**：回顾本轮对话，分析每个**已发言**玩家的发言逻辑和投票行为，找出可疑之处。思考你的身份该如何发言（狼人伪装好人？神职报信息？平民分析推理？）。\n"
                "   **特别注意**：前面发言的玩家已经说过什么？不要重复他们的话！找到他们没提到的角度，或者直接点名你同意/不同意谁。\n"
                "2. **检查你的逻辑链**：在开口之前，自问三个问题：\n"
                "   ① 我指控/信任某人的**具体依据**是什么？（必须是他说的某句话、投的某次票，不能是\"感觉\"）\n"
                "   ② 从这个依据到我结论的**推理过程**是什么？（每一步都经得起反驳吗？）\n"
                "   ③ 我的推理有没有**隐含前提**未经证实？（比如\"说话完美=狼\"——凭什么？）\n"
                "   **如果你的逻辑链任何一环答不上来，重新思考，不要急着发言。**\n"
                "3. **再调用函数发言**：逻辑链确认无误后，调用 `speak` 函数发表你的发言。\n"
                "   - 发言要有风格：选择激进攻击、理性分析、情绪渲染等风格之一\n"
                "   - 发言要有对抗：直接点名你的怀疑对象，说出具体理由\n"
                "   - 发言要有个性：用自己的语言，不要套模板\n"
                "   - 发言不超过200字，但要有实质内容\n"
                "   - **发言至少15字！只说\"过\"或\"就这样\"是绝对不可接受的！**\n"
                "   - **严禁使用以下模板式开场**：\"信息不多\"、\"大家多发言别划水\"、\"暂时没有明确怀疑对象\"、\"先听后面发言\"、\"X号发言比较正暂时认好\"\n"
                "   - **发言必须有实质内容**：\n"
                "     - **如果你有信息**（查验结果、刀口信息、狼队友情报）：基于你的信息进行分析，点名你的怀疑对象并说明具体理由\n"
                "     - **如果你是平民且没有信息**：说实话！可以说“我是平民，这轮没有额外信息，先听听大家怎么说”——信息少不是错，诚实比胡乱指认更有价值\n"
                "     - **严禁在没有依据的情况下胡乱怀疑**：没有信息就去分析已发言玩家的逻辑漏洞和投票行为，而不是凭空指认某人是狼。乱踩好人只会帮狼人分散火力\n"
                "   - **给出自己的独立判断而非复述他人**\n"
                "   - **如果同意前面的观点，必须补充新理由**\n"
                "**如果你是预言家，特别注意**：\n"
                "  - 跳明身份之前必须完成深度分析——你查过谁、结果是什么、现在报信息是否必要、跳了会不会当晚被刀。\n"
                "  - 如果决定跳身份报查验，**只报查验结果本身**。不要附加行为分析来\"佐证\"——查验就是最硬的证据，画蛇添足的佐证只会给狼人送你逻辑漏洞。\n"
                "  - 预言家的命比单条信息重要，不要因为想说话就暴露。只有查杀到狼人、或场上信息极度混乱需要你来带方向时，才考虑亮身份。\n"
                "**注意：必须调用 speak 函数来发言，不要直接输出文本！直接输出文本将被系统拒绝！**"
            )

        if context == "last_words":
            # If hunter: note that shooting has already been resolved
            hunter_note = ""
            if "hunter" in role_name:
                hunter_note = (
                    "**你是猎人，你的开枪决定已经在此遗言之前做出了。**"
                    "如果你的特权信息显示猎枪已用，说明你已经开枪带走了某人；"
                    "如果猎枪仍可用，说明你选择了压枪（不开枪）。"
                    "请在遗言中告知好人你的决定和理由。\n"
                )
            return (
                "## 你的任务：遗言\n"
                "你已出局，这是你最后的机会。请按以下步骤操作：\n"
                f"{hunter_note}"
                "1. **先深度思考**：回顾整局游戏，梳理你的身份信息、查验结果（如有）、你的推理和怀疑对象。想清楚你要告诉好人什么信息。\n"
                "2. **再调用函数发表遗言**：思考完毕后，调用 `last_words` 函数发表你的遗言。报出你的分析、怀疑对象和给好人的建议。\n"
                "**注意：必须调用 last_words 函数来发表遗言，不要直接输出文本！发表后你将不能再次发言。**"
            )

        if context == "exile_vote":
            return (
                "## 你的任务：放逐投票\n"
                "现在是放逐投票。选择一个你想放逐的玩家，或0号弃权。\n"
                '必须严格按JSON格式输出：{"action_type":"vote","target_seat":<目标座位号>,"reasoning":"<你的推理>"}'
                ' 或 {"action_type":"abstain","target_seat":null,"reasoning":"<你的理由>"} 表示弃权。'
            )

        return "请根据你的身份和当前局势做出合理决策。"

    # ── Name / camp helpers ────────────────────────────────────

    def _cn_name(self, role_name: str) -> str:
        mapping = {
            "wolf-killer-werewolf": "狼人", "wolf-killer-villager": "平民",
            "wolf-killer-seer": "预言家", "wolf-killer-witch": "女巫",
            "wolf-killer-hunter": "猎人",
        }
        return mapping.get(role_name, role_name)

    def _cn_camp_of(self, role_name: str) -> str:
        if "werewolf" in role_name:
            return "狼人阵营"
        return "好人阵营"

    def _cn_phase(self, phase: str) -> str:
        mapping = {
            "waiting": "等待中", "role_deal": "角色分配", "night": "夜晚",
            "dawn": "天亮", "last_words": "遗言", "speech": "发言阶段",
            "vote_casting": "投票阶段", "vote_resolution": "投票结算", "game_over": "游戏结束",
        }
        return mapping.get(phase, phase)
