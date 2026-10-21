"""Evil Knight: a werewolf that shrugs off the night and bounces two skills back.

Three abilities, none of which needs role-specific engine code:

* **Night immunity** travels as the ``night_immune`` marker, which
  ``core/night_settlement.py`` honours for *every* lethal night effect and which
  the shared kill contract refuses as a target.
* **Cannot self-destruct / cannot self-kill** — the role simply declares no
  explode contract, and ``self_kill_forbidden`` rejects its own ballot.
* **One-shot revenge** is the ``core/reflect.py`` protocol: the seer and the
  witch see ``reflect_check`` / ``reflect_poison`` on the target and bounce the
  damage back at themselves, consuming ``reflect_used`` so a second skill the
  same night lands normally.

Official naming: 2024 archives call this role 恶灵骑士 and 2025/2026 archives
call it 恶夜骑士; they are the same card, so the id stays ``evil-knight``.
"""

from __future__ import annotations

from app.models.pipeline import EffectKind, RoleSpec
from app.roles.werewolf import SELF_KILL_FORBIDDEN, WEREWOLF_KILL_CONTRACT, Werewolf

EVIL_KNIGHT_SPEC = RoleSpec(
    tags=frozenset({"wolf"}),
    role_id="wolf-killer-evil-knight", display_name="Evil Knight",
    camp_id="werewolf",
    contracts=(WEREWOLF_KILL_CONTRACT,),
    initial_resources={
        # Permanent immunity to every lethal night effect.
        "night_immune": 1,
        # The two skills it bounces, and the one-shot marker that arms them.
        "reflect_check": 1,
        "reflect_poison": 1,
        "reflect_used": 0,
        # It may never be the pack's own kill target.
        SELF_KILL_FORBIDDEN: 1,
    },
    allowed_effects=frozenset({EffectKind.SUBMIT_DAMAGE, EffectKind.EMIT_EVENT}),
    visibility_namespaces=frozenset({"PUBLIC", "ACTOR", "CAMP"}),
    max_count=1,
    # The card forbids self-exposure; the election badge move is the same act.
    self_explode_forbidden=True,
    instructions=(
        "你是恶夜骑士（狼人阵营）：夜晚与狼队共用同一次狼刀，按阵营合并计票。"
        "你永久免疫一切夜间伤害——狼刀、女巫的毒药都杀不死你，狼队也不能把你选为刀口。"
        "你不能自爆，也不能自刀。"
        "你只有一次反伤机会：被预言家查验时，预言家当夜出局；被女巫下毒时，女巫当夜出局；"
        "同一夜两者都命中只发动一次——**查验先结算**，所以同夜命中时预言家先出局、女巫不会出局，"
        "之后你的反伤永久消失。"
        "反伤是自动触发的，你无法选择反给谁。守卫的守护挡不住反伤，"
        "女巫的解药也救不活被反伤出局的人。"
        "战术提示：你是狼队的强神，前期尽量隐藏身份；被查验或被毒时反伤会立刻暴露你，"
        "因此要权衡「换掉一个神职」与「继续潜伏」的收益。"
    ),
)


class EvilKnight(Werewolf):
    """Evil Knight: immunity and revenge are handled by the pipeline contracts.

    It deliberately declares no explode contract, which is what makes
    "cannot self-destruct" true without any engine branch.
    """

    def get_skills(self) -> list[str]:
        return ["kill"]
