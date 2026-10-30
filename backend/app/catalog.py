"""Role display metadata, standard field presets, and custom-field constraints.

Kept OUTSIDE the frozen RoleSpec so adding names/icons/presets never changes
the registry digest and never invalidates persisted game archives.

``rules`` holds the player-facing rule bullets shown on the in-app rules page
(``frontend/src/components/rules/``). It is display text only: never read it
from the pipeline, and never move it into a ``RoleSpec`` (that would change the
frozen digest).
"""

from __future__ import annotations

from typing import Any

ROLE_METADATA: dict[str, dict[str, Any]] = {
    "wolf-killer-werewolf": {
        "name_zh": "狼人", "name_en": "Werewolf", "icon": "wolf",
        "description": "夜晚睁眼，与狼队友商议刀人",
        "description_en": "Opens eyes at night and plans the kill with the pack",
        "rules": [
            "夜晚与所有狼队友（含白狼王、狼美人）共用同一次狼刀：先在狼队频道讨论，再各自投票决定当晚刀口，可以空刀。",
            "狼人彼此知道身份；胜利条件是清空神职、清空平民，或让存活狼人数超过存活好人数。",
        ],
        "rules_en": [
            "At night you share a single kill with all wolf teammates (including the White Wolf King and Wolf Beauty): discuss in the wolf channel first, then each votes on the target; abstaining is allowed.",
            "Werewolves know each other's identities; you win by eliminating all gods, all villagers, or outnumbering the surviving good players.",
        ],
    },
    "wolf-killer-villager": {
        "name_zh": "平民", "name_en": "Villager", "icon": "villager",
        "description": "白天发言投票，找出狼人",
        "description_en": "Speaks and votes by day to find the werewolves",
        "rules": [
            "没有夜间技能，夜晚全程闭眼。",
            "靠白天发言与放逐投票找出狼人；计入平民，是狼人屠边的目标之一。",
        ],
        "rules_en": [
            "No night skill; your eyes stay closed all night.",
            "Find the werewolves through daytime speeches and exile votes; you count as a villager — a target of the wolves' edge victory.",
        ],
    },
    "wolf-killer-seer": {
        "name_zh": "预言家", "name_en": "Seer", "icon": "seer",
        "description": "每晚查验一名玩家的阵营",
        "description_en": "Checks one player's alignment each night",
        "rules": [
            "每晚查验一名存活玩家的阵营（好人 / 狼人），查验结果只有自己可见。",
            "没有自保能力，需要靠发言把自己验出的信息传递给好人。",
        ],
        "rules_en": [
            "Each night, check one living player's alignment (good / werewolf); the result is visible only to you.",
            "No self-protection — you must pass your findings to the good side through speeches.",
        ],
    },
    "wolf-killer-witch": {
        "name_zh": "女巫", "name_en": "Witch", "icon": "witch",
        "description": "拥有一瓶解药和一瓶毒药",
        "description_en": "Holds one antidote and one poison",
        "rules": [
            "全局各有一瓶解药与一瓶毒药，且每晚至多使用一瓶。",
            "只要存活，每晚都能看到当晚狼刀目标（药已用完也能看到，刀口为空代表空刀）。",
            "解药只能救当晚刀口上的人，自救仅限第一晚；毒药可毒任意存活玩家，无视守卫的守护。",
            "守卫守护与解药同时落在同一刀口上时，该玩家仍然死亡（对穿）。",
        ],
        "rules_en": [
            "You hold one antidote and one poison for the whole game, and may use at most one per night.",
            "While alive you see each night's wolf target (even after both potions are spent; an empty target means the wolves abstained).",
            "The antidote only saves that night's victim — self-save is allowed on night one only; the poison can hit any living player and ignores the Guard's shield.",
            "If the Guard's shield and your antidote land on the same wolf target, that player still dies (the stack backfires).",
        ],
    },
    "wolf-killer-awakened-witch": {
        "name_zh": "觉醒女巫", "name_en": "Awakened Witch", "icon": "witch",
        "description": "好人神职，一瓶解药和三瓶调配毒药；毒药需要全部协助者同意才生效",
        "description_en": "Good god role; one antidote and three brewed poisons that work only if every assistant agrees",
        "rules": [
            "属于好人阵营神职。拥有一瓶解药和三瓶毒药，每晚只能选择其中一个技能使用。",
            "解药只能救当晚被狼人刀杀的玩家；12 人场全程不能自救。解药与守卫的守护同时落在同一刀口上时仍然死亡（同守同救）。",
            "毒药：先选一名存活玩家作为毒杀目标，再按第 1 / 2 / 3 次分别选择 1 / 2 / 3 名存活玩家作为协助者。"
            "每名玩家整局只能协助一次；可以选自己，也可以选毒药目标。",
            "协助者知道彼此座位与被毒目标，≥2 名协助者时可以短暂交流；他们不知道彼此的身份，也不知道彼此的选择。"
            "觉醒女巫不知道协助者的选择结果；若她不在协助者中，也不参与交流。",
            "所有协助者都选择「毒」时毒药才生效；任意一人选择「不毒」或未表态，毒药就不生效。"
            "无论是否生效，发动一次毒药调配就消耗一次次数。",
            "毒药无视守卫的守护；被毒死的猎人不能开枪，被毒死的狼王/觉醒狼王不能发动狼王爪。",
        ],
        "rules_en": [
            "A god of the good camp. You hold one antidote and three poisons, and may use only one skill per night.",
            "The antidote only saves that night's wolf victim; on 12-player boards self-save is never allowed. Antidote plus the Guard's shield on the same target still kills (the stack backfires).",
            "Poison: pick a living victim, then pick 1 / 2 / 3 living assistants for your 1st / 2nd / 3rd brew. Each player may assist only once per game; you may pick yourself or the victim.",
            "Assistants learn each other's seats and the victim; with 2 or more they may briefly confer, but they know neither each other's roles nor each other's votes. The Awakened Witch never learns how they voted and joins the conferral only if she is one of them.",
            "The poison works only if every assistant chooses 'poison'; one 'don't poison' or abstention nullifies it. Either way, the brew attempt is consumed.",
            "The poison ignores the Guard's shield; a poisoned Hunter cannot shoot, and a poisoned Wolf King / Awakened Wolf King cannot fire the Wolf Claw.",
        ],
    },
    "wolf-killer-hunter": {
        "name_zh": "猎人", "name_en": "Hunter", "icon": "hunter",
        "description": "死亡时可以开枪带走一名玩家",
        "description_en": "May shoot and take a player down on death",
        "rules": [
            "出局时可以开枪带走一名存活玩家，也可以选择不开枪（开枪不是强制的）。",
            "可以开枪的死因：被狼刀、被放逐、被其他猎人开枪带走、被白狼王自爆带走。",
            "被女巫毒杀、被骑士决斗致死、被狼美人殉情带走时不能开枪。",
        ],
        "rules_en": [
            "On elimination you may shoot a living player — or hold your fire (the shot is optional).",
            "Causes that allow the shot: wolf kill, exile, another Hunter's shot, the White Wolf King's self-destruct.",
            "No shot when poisoned by the Witch, killed in the Knight's duel, or taken by the Wolf Beauty's charm.",
        ],
    },
    "wolf-killer-awakened-hunter": {
        "name_zh": "觉醒猎人", "name_en": "Awakened Hunter", "icon": "hunter",
        "description": "出局后选择正序或逆序巡猎，方向上最近的一名狼人出局",
        "description_en": "After elimination, sweeps in seat order and kills the nearest werewolf",
        "rules": [
            "出局后可以发动「巡猎」：选择**正序**（座位号从小到大）或**逆序**（从大到小，绕圈）方向，"
            "该方向上**最近的一名狼人**出局；也可以选择不发动。",
            "**任何出局方式**都能发动（被狼刀、被放逐、被女巫毒杀、被决斗、被带走），"
            "这一点与普通猎人只有「被狼刀 / 被放逐」可开枪不同。",
            "如果是在**夜间**出局，白天发动巡猎时**无视特殊狼人的保命技能**"
            "（例如觉醒狼美人的「挽歌幻象」替代）。",
            "子弹必定命中狼人，不会误伤好人：你只需要判断方向，不需要指认具体座位。",
            "属于好人阵营的**神职**。",
        ],
        "rules_en": [
            "On elimination you may launch a Sweep: choose ascending or descending seat order (wrapping around), and the nearest werewolf in that direction dies; firing is optional.",
            "Any cause of death triggers it (wolf kill, exile, poison, duel, being taken along) — unlike the normal Hunter, who can only shoot after a wolf kill or exile.",
            "If you died at night, the daytime Sweep ignores special wolves' survival skills (such as the Awakened Wolf Beauty's stand-in illusion).",
            "The bullet always hits a werewolf and never a good player: you only choose the direction, not a seat.",
            "A god of the good camp.",
        ],
    },
    "wolf-killer-guard": {
        "name_zh": "守卫", "name_en": "Guard", "icon": "guard",
        "description": "每晚守护一名玩家免于狼刀",
        "description_en": "Shields one player from the wolf kill each night",
        "rules": [
            "每晚守护一名存活玩家（也可以不守），但不能连续两晚守护同一人。",
            "守护只抵消狼刀：毒药与猎枪无视守护。",
            "与女巫解药同时作用于同一刀口时目标仍死亡（对穿）。",
        ],
        "rules_en": [
            "Each night, shield one living player (you may skip), but never the same player two nights in a row.",
            "The shield only blocks the wolf kill: poison and the Hunter's shot ignore it.",
            "If your shield and the Witch's antidote land on the same wolf target, that player still dies (the stack backfires).",
        ],
    },
    "wolf-killer-idiot": {
        "name_zh": "白痴", "name_en": "Idiot", "icon": "idiot",
        "description": "被投票出局时翻牌免死，但失去投票权",
        "description_en": "Flips the card to survive an exile vote, but loses the vote",
        "rules": [
            "被放逐时翻牌：公开身份并免于出局，但仍可发言。",
            "翻牌后失去投票权，也不再出现在放逐候选中（不会被再次放逐）。",
            "被狼刀、被毒杀、被枪杀或被自爆带走时正常死亡；翻牌后只要存活就仍计入神职。",
        ],
        "rules_en": [
            "When exiled, flip your card: reveal your role, survive, and keep speaking.",
            "After flipping you lose your vote and can no longer be nominated for exile.",
            "Wolf kills, poison, gunshots, and self-destruct still take you out normally; once flipped you keep counting as a god while alive.",
        ],
    },
    "wolf-killer-werewolf-king": {
        "name_zh": "白狼王", "name_en": "White Wolf King", "icon": "wolf_king",
        "description": "狼人，白天发言阶段可自爆带走一名玩家",
        "description_en": "Werewolf; may self-destruct during daytime speeches to take one player down",
        "rules": [
            "夜晚与狼队共用同一次狼刀，按阵营合并计票。",
            "白天每位玩家开口发言前可以自爆一次并带走一名存活玩家：双方都没有遗言，当日剩余发言与投票全部取消，直接进入夜晚。",
            "被毒杀、被放逐或被枪杀时正常出局，不能带人。",
        ],
        "rules_en": [
            "At night you share the pack's single kill, with votes merged across the camp.",
            "Before any player's daytime speech you may self-destruct once and take a living player with you: neither side gets last words, the day's remaining speeches and vote are cancelled, and play goes straight to night.",
            "When poisoned, exiled, or shot you die normally and take no one with you.",
        ],
    },
    "wolf-killer-wolf-king": {
        "name_zh": "狼王", "name_en": "Wolf King", "icon": "wolf_king",
        "description": "狼人，出局时可以发动一次狼王爪带走一名玩家",
        "description_en": "Werewolf; on elimination may fire the Wolf Claw once to take a player down",
        "rules": [
            "夜晚与所有狼队友共用同一次狼刀，按阵营合并计票。",
            "出局时可以发动一次「狼王爪」带走一名存活玩家（也可以不发动）。",
            "可以发动的出局方式：夜间被狼刀、白天被放逐、被猎人开枪带走。",
            "不能发动的出局方式：自爆、被女巫毒杀、被狼美人殉情带走、被骑士决斗出局。",
            "发动时不需要翻牌；被带走的猎人仍然可以开枪反击。",
        ],
        "rules_en": [
            "At night you share the pack's single kill, with votes merged across the camp.",
            "On elimination you may fire the Wolf Claw once to take a living player with you (firing is optional).",
            "Triggers on: night wolf kill, daytime exile, or the Hunter's shot.",
            "Does not trigger on: self-destruct, poison, the Wolf Beauty's charm, or the Knight's duel.",
            "No reveal is needed to fire; a Hunter taken by the Claw may still shoot back.",
        ],
    },
    "wolf-killer-awakened-wolf-king": {
        "name_zh": "觉醒狼王", "name_en": "Awakened Wolf King", "icon": "wolf_king",
        "description": "狼人，拥有两次狼王爪，可亲传队友；不亲传时出局可带走两人",
        "description_en": "Werewolf; holds two Wolf Claws that can be passed to teammates — kept, they take two players down on death",
        "rules": [
            "属于狼人阵营，夜晚与狼队共同讨论和刀人；可以自爆身份，也可以自刀。",
            "拥有两次「狼王爪」：每晚可以把一次爪亲传给一名存活、尚未持爪的狼窝队友；"
            "每名队友整局最多获得一次，不能传给自己，隐狼不在传递范围。",
            "不亲传时，出局后可以一次发动双爪带走两名存活玩家；已经传出一爪，则只剩一次单爪。",
            "可以发动的出局方式：夜间被狼刀、白天被放逐、被猎人开枪带走；"
            "被毒、自爆、被狼美人殉情带走、被骑士决斗出局时不能发动；成为最后一狼时也不能发动。",
            "被狼王爪带走的人死因是「狼王爪」；被带走的猎人不能开枪，被带走的狼美人不会殉情。",
            "白天发言前可以自爆：公开身份、自己出局、当日结束、不带走任何人，且不触发狼王爪。"
            "警长竞选期也可以自爆吞警徽。",
        ],
        "rules_en": [
            "Wolf camp: discuss and kill with the pack at night; self-destruct and self-kill are allowed.",
            "You hold two Wolf Claws: each night you may pass one Claw to a living pack teammate who holds none yet; each teammate can receive at most one per game, you can't pass to yourself, and hidden wolves are out of range.",
            "If you keep both, on elimination you fire both Claws to take two living players with you; with one passed away, only a single Claw remains.",
            "Triggers on night wolf kill, daytime exile, or the Hunter's shot; not on poison, self-destruct, the Wolf Beauty's charm, or the Knight's duel — and not when you are the last wolf standing.",
            "Victims die to the 'Wolf Claw'; a Hunter taken this way cannot shoot, and a Wolf Beauty taken this way charms no one along.",
            "Before daytime speeches you may self-destruct: reveal your role, die, end the day, take no one, and skip the Claw. Self-destructing during the sheriff race swallows the badge.",
        ],
    },
    "wolf-killer-evil-knight": {
        "name_zh": "恶夜骑士", "name_en": "Evil Knight", "icon": "evil_knight",
        "description": "狼人，无法在夜间出局（猎枪除外）；被查验或下毒时反伤一次",
        "description_en": "Werewolf; cannot die at night (except to the Hunter's shot) and reflects the first check or poison",
        "rules": [
            "夜晚与狼队共用同一次狼刀，按阵营合并计票。",
            "**无法在夜间出局**：狼刀与女巫的毒药都杀不死你，狼队也不能把你选为刀口。"
            "唯一的例外是**猎枪** —— 猎人在白天公布死讯时开枪，那一枪能带走你。",
            "不能自爆，也不能自刀。",
            "只有一次反伤机会：被预言家查验则预言家当夜出局，被女巫下毒则女巫当夜出局；"
            "同一夜两者都命中只发动一次——**查验先结算**，所以同夜命中时预言家先出局、"
            "女巫不会出局，之后反伤永久消失。",
            "反伤是自动触发的，恶夜骑士无法选择反给谁。守卫的守护挡不住反伤；"
            "女巫的解药也救不活被反伤出局的人。",
            "旧称「恶灵骑士」，是同一张牌。",
        ],
        "rules_en": [
            "At night you share the pack's single kill, with votes merged across the camp.",
            "Cannot die at night: wolf kills and the Witch's poison can't kill you, and the pack can't pick you as the target. The only exception is the Hunter's shot fired at daytime death announcements.",
            "Cannot self-destruct or self-kill.",
            "One reflection only: a Seer's check kills the Seer that night, a Witch's poison kills the Witch that night; if both hit the same night it fires once — the check resolves first, so the Seer dies and the Witch survives, then the reflection is gone for good.",
            "Reflection triggers automatically; you don't choose its target. The Guard's shield can't block it, and the Witch's antidote can't save its victim.",
            "Formerly called the 'Ghost Knight' — the same card.",
        ],
    },
    "wolf-killer-dream-weaver": {
        "name_zh": "摄梦人", "name_en": "Dream Weaver", "icon": "dream_weaver",
        "description": "好人神职，每晚让一名玩家梦游，梦游者免疫夜间伤害",
        "description_en": "Good god role; sends one player sleepwalking each night — the dreamer is immune to night damage",
        "rules": [
            "每晚必须指定一名自己以外的存活玩家为梦游者，不可放弃。",
            "梦游者本人不会知道自己被摄梦，也看不到任何提示。",
            "梦游者当夜免疫一切夜间伤害——狼刀和女巫的毒药都杀不死他。",
            "连续两晚选择同一名玩家，该玩家会在第二夜出局（死因「摄梦」）。",
            "摄梦人在夜间出局时，当夜的梦游者与之同亡；白天被放逐或被骑士决斗出局时梦游者不受影响。",
        ],
        "rules_en": [
            "Each night you must pick a living player other than yourself as the dreamer; skipping is not allowed.",
            "The dreamer is never told and sees no hint.",
            "The dreamer is immune to all night damage — neither the wolf kill nor the Witch's poison can kill them.",
            "Pick the same player two nights in a row and that player dies on the second night (cause: 'dreamt away').",
            "If you die at night, that night's dreamer dies with you; exile or the Knight's duel leaves the dreamer untouched.",
        ],
    },
    "wolf-killer-gargoyle": {
        "name_zh": "石像鬼", "name_en": "Gargoyle", "icon": "gargoyle",
        "description": "狼人，不属于狼窝；每晚查验一名玩家的具体身份",
        "description_en": "Werewolf outside the pack; checks one player's exact role each night",
        "rules": [
            "你不属于狼窝：不参与狼队讨论与狼刀投票，其他狼不知道你是谁，你也看不到他们是谁，"
            "只知道场上还剩几只狼。",
            "因此你可能亲手刀死自己的狼队友；当其他狼全部出局后，你获得带刀权。",
            "不能自爆，也不能吞警徽。",
            "每晚可以查验一名存活玩家的具体身份，不能查自己，也不能重复查同一人；结果只有你知道。",
            "被预言家查验时你会显示为狼人。",
        ],
        "rules_en": [
            "You are not in the pack: no wolf chat, no kill vote — the other wolves don't know you and you can't see them; you only know how many wolves remain.",
            "So you might personally knife a wolf teammate; once every other wolf is out, you gain the kill.",
            "Cannot self-destruct or swallow the sheriff badge.",
            "Each night, check one living player's exact role — never yourself, never the same player twice; only you see the result.",
            "The Seer's check shows you as a werewolf.",
        ],
    },
    "wolf-killer-gravekeeper": {
        "name_zh": "守墓人", "name_en": "Gravekeeper", "icon": "gravekeeper",
        "description": "好人神职，每晚获知上一个被放逐者的阵营",
        "description_en": "Good god role; each night learns the last exiled player's alignment",
        "rules": [
            "没有任何主动技能，白天像普通平民一样发言与投票。",
            "每晚自动获知上一个白天被放逐玩家的阵营（好人 / 狼人），不需要做选择，也不消耗资源。",
            "第一个夜晚还没有人被放逐，因此当晚没有信息。",
            "被放逐的石像鬼会显示为狼人。",
        ],
        "rules_en": [
            "No active skill; speak and vote like a normal villager by day.",
            "Each night you automatically learn the alignment (good / werewolf) of the last exiled player — no choice needed, nothing consumed.",
            "Nobody has been exiled before night one, so you learn nothing that night.",
            "An exiled Gargoyle shows as a werewolf.",
        ],
    },
    "wolf-killer-knight": {
        "name_zh": "骑士", "name_en": "Knight", "icon": "knight",
        "description": "发言结束后投票前翻牌决斗一名玩家，是狼人则白天立即结束",
        "description_en": "After speeches and before the vote, flips to duel one player — if a wolf, the day ends at once",
        "rules": [
            "一局一次：全体发言结束、放逐投票开始前，翻牌决斗一名存活玩家（也可以不发动）。",
            "对方是狼人 → 该玩家立即死亡，并在进入夜晚前发表遗言；当日余下流程（含投票）取消，直接进入夜晚。",
            "对方是好人 → 骑士以死谢罪且没有遗言，当日投票照常进行。",
        ],
        "rules_en": [
            "Once per game: after all speeches, before the exile vote, flip your card and duel a living player (you may hold back).",
            "If the target is a werewolf, they die at once and give last words before nightfall; the rest of the day (including the vote) is cancelled.",
            "If the target is good, the Knight dies in disgrace with no last words, and the day's vote proceeds normally.",
        ],
    },
    "wolf-killer-wolf-beauty": {
        "name_zh": "狼美人", "name_en": "Wolf Beauty", "icon": "wolf_beauty",
        "description": "狼人，每晚可魅惑一名好人，出局时被魅惑者殉情",
        "description_en": "Werewolf; may charm a good player each night — the charmed one dies with you",
        "rules": [
            "夜晚与狼队共用同一次狼刀；狼刀之后可以额外魅惑一名好人（也可以不魅）。",
            "不能连续两晚魅惑同一人，不能魅惑自己、狼队友或免疫魅惑的老酒鬼。",
            "出局时把当晚被魅惑的玩家一起带走（殉情）；被骑士决斗出局时不带走任何人。殉情者没有遗言，被带走的猎人也不能开枪。",
            "不能自爆，也不能成为狼队的刀口。",
        ],
        "rules_en": [
            "At night you share the pack's single kill; after the kill you may charm one good player (charming is optional).",
            "Never charm the same player two nights running; you can't charm yourself, wolf teammates, or the charm-immune Old Drunkard.",
            "On elimination you take that night's charmed player with you (bound by love); the Knight's duel takes no one. The charmed get no last words, and a charmed Hunter cannot shoot.",
            "Cannot self-destruct, and the pack can't pick you as the target.",
        ],
    },
    "wolf-killer-lonely-girl": {
        "name_zh": "孤独少女", "name_en": "Lonely Girl", "icon": "villager",
        "description": "平民，第一夜最先选择一名崇拜对象；胜负跟随崇拜对象的阵营",
        "description_en": "Villager; first to act on night one, picking an idol — your result follows the idol's camp",
        "rules": [
            "属于好人阵营、计入**平民**（不计入神职）：预言家查验为好人，放逐投票与普通平民完全一样。",
            "**第一夜最开始时最先行动**：选择一名其他存活玩家作为崇拜对象，整局只选一次、不可更改。",
            "崇拜对象不会知道自己被崇拜，你也不会再从技能里得到任何信息。",
            "**胜负跟随崇拜对象所在阵营**：崇拜对象最终获胜则你一起获胜（哪怕你自己的阵营输了）；"
            "崇拜对象落败则你也落败。",
            "因此你没有独立获胜的方式：这张牌赌的是「最后赢的那一方」。",
        ],
        "rules_en": [
            "Good camp, counted as a villager (not a god): the Seer reads you as good, and exile votes treat you like any villager.",
            "First to act on night one: pick another living player as your idol — once per game, no changes.",
            "The idol never learns of the worship, and the skill gives you nothing more.",
            "Your result follows the idol's camp: if the idol's side wins, you win too (even if your own camp lost); if the idol's side loses, so do you.",
            "So you have no path of your own: this card bets on whoever wins in the end.",
        ],
    },
    "wolf-killer-miracle-merchant": {
        "name_zh": "奇迹商人", "name_en": "Miracle Merchant", "icon": "guard",
        "description": "好人神职，每晚最先行动：让一名玩家成为幸运儿并赠予一个一次性技能；赠给狼人则次日自己出局",
        "description_en": "Good god role; first to act each night — grants a player a one-shot skill, but granting a wolf kills you the next day",
        "rules": [
            "属于好人阵营、计入**神职**，每晚**最先行动**。",
            "每晚可选一名其他存活玩家成为**幸运儿**，并从三个一次性技能里赠予一个："
            "预言家的查验、女巫的毒药、守卫的守护。**每个技能整局只能赠出一次**，所以最多赠三次。",
            "幸运儿保留自己的身份、阵营与胜利条件，只是多了一次可用的技能：当晚或之后任意一晚使用皆可，"
            "用掉就没有了。幸运儿得到的技能，其余玩家（含奇迹商人本人）都不会得知它被如何使用。",
            "**若幸运儿是狼人阵营**：TA 不会获得技能，**次日全体发言结束后奇迹商人出局**（死因为奇迹反噬，"
            "与猎魔人的「延迟出局」同一时机）；这个技能同样算作已经赠出。",
            "可以选择不赠予，把技能留到更有把握的一晚。",
        ],
        "rules_en": [
            "Good camp, counted as a god, and acts first each night.",
            "Each night you may make another living player the Lucky One and grant one of three one-shot skills: the Seer's check, the Witch's poison, or the Guard's shield. Each skill can be granted only once per game — three gifts at most.",
            "The Lucky One keeps their role, camp, and win condition — they just gain one usable skill, that night or any later night, spent once used. Nobody else (including you) learns how the granted skill was used.",
            "If the Lucky One is a werewolf: they get nothing and the Miracle Merchant dies after the next day's speeches (cause: miracle backlash, same timing as the Demon Hunter's delayed death); the skill still counts as granted.",
            "You may hold the gift for a better night.",
        ],
    },
    "wolf-killer-mirror-girl": {
        "name_zh": "魔镜少女", "name_en": "Mirror Girl", "icon": "seer",
        "description": "好人神职，每晚可查验一名玩家的具体身份",
        "description_en": "Good god role; may check one player's exact role each night",
        "rules": [
            "属于好人阵营的**神职**。每晚可以查验一名存活玩家（不能是自己），得知其**具体身份**，而不只是阵营。",
            "查验结果只有你自己知道。你无法向全场证明它，何时公开、如何公开由你判断。",
            "被觉醒隐狼模仿过的玩家，在魔镜里显示为**被模仿的身份**：如果你的查验结果与已知信息矛盾，这本身就是线索。",
            "被查验的石像鬼显示为石像鬼；每晚的查验不会让对方得知。",
        ],
        "rules_en": [
            "A god of the good camp. Each night you may check one living player (not yourself) and learn their exact role, not just their alignment.",
            "Only you see the result. You can't prove it to the table — when and how to reveal it is your call.",
            "A player mimicked by the Awakened Hidden Wolf shows the mimicked role in the mirror: a result that contradicts known facts is itself a clue.",
            "A checked Gargoyle shows as a Gargoyle; your checks are never detected.",
        ],
    },
    "wolf-killer-awakened-hidden-wolf": {
        "name_zh": "觉醒隐狼", "name_en": "Awakened Hidden Wolf", "icon": "gargoyle",
        "description": "狼人阵营但不属于狼窝，整局一次模仿一名玩家：立刻获得其身份，下一夜起获得其技能",
        "description_en": "Wolf camp but outside the pack; once per game, mimic a player — take their shown identity at once and their skill from the next night",
        "rules": [
            "属于狼人阵营，但**不属于狼窝**：不参与狼队夜间讨论与狼刀投票，不知道其他狼是谁，其他狼也不知道你是谁（只知道场上还剩几只狼）。不能自爆。",
            "其他狼全部出局后，你获得带刀权，每晚可以独自刀人。",
            "**整局一次**，夜间可以模仿一名其他存活玩家（官方资料没有写明次数或冷却，按一局一次实现）。",
            "**身份立刻生效**：模仿成功后，所有查验（预言家、魔镜少女、纯白之女等）都把你显示成被模仿者的身份与阵营；"
            "你自己的阵营、投票与胜负归属不变。",
            "**技能从下一夜起生效**：预言家的查验、女巫的解药与毒药（各一瓶）、守卫的守护、猎人的枪、魔镜少女的具体身份查验。"
            "模仿没有夜间技能的角色（如平民）只改变显示，不给技能。",
            "猎人枪的触发条件与猎人一致：只有夜间被狼刀或白天被放逐才能开枪。",
        ],
        "rules_en": [
            "Wolf camp but not in the pack: no wolf chat or kill vote, you don't know the other wolves and they don't know you (only how many remain). Cannot self-destruct.",
            "Once all other wolves are out, you gain the kill and strike alone each night.",
            "Once per game, at night, you may mimic another living player (the official card text doesn't state a count or cooldown — implemented as once per game).",
            "The disguise applies at once: after mimicking, every check (Seer, Mirror Girl, Pure White, etc.) shows you as the copied role and camp; your real camp, vote, and win condition don't change.",
            "The skill works from the next night: the Seer's check, the Witch's antidote and poison (one each), the Guard's shield, the Hunter's gun, the Mirror Girl's exact-role check. Mimicking a role with no night skill (like a villager) only changes the display.",
            "A copied Hunter gun follows the Hunter's triggers: it fires only on a night wolf kill or daytime exile.",
        ],
    },
    "wolf-killer-radiance-earl": {
        "name_zh": "流光伯爵", "name_en": "Radiance Earl", "icon": "guard",
        "description": "好人神职，从第二晚起每晚可庇护一名其他玩家，免疫一切夜间伤害（含毒药与梦游）",
        "description_en": "Good god role; from night two, may shelter another player each night — immune to all night damage, poison and dreaming included",
        "rules": [
            "属于好人阵营的**神职**。**从第二晚开始**，每晚可以选择一名**其他**存活玩家进行流光庇护。",
            "被庇护的玩家当夜**免疫一切夜间伤害**：狼刀、女巫的毒药、摄梦人的梦游伤害都伤不到他（普通守卫的守护挡不住毒药，流光庇护可以）。",
            "可以选择不使用；**不能连续两晚庇护同一个人**，中间隔一晚则可以。被庇护的玩家本人不会知道。",
            "如果蚀日侍女吞噬了你的技能，或吞噬了你当晚庇护的玩家，她会受到**流光反噬**，次日全体发言结束后出局。",
        ],
        "rules_en": [
            "A god of the good camp. From night two on, each night you may shelter another living player with radiance.",
            "A sheltered player is immune to all night damage that night: the wolf kill, the Witch's poison, and the Dream Weaver's damage all fail (where the Guard's shield can't stop poison, radiance can).",
            "Sheltering is optional; never shelter the same player two nights running — a gap night is fine. The sheltered player is never told.",
            "If the Eclipse Maid devours your skill, or devours the player you sheltered that night, she suffers radiance backlash and dies after the next day's speeches.",
        ],
    },
    "wolf-killer-eclipse-maid": {
        "name_zh": "蚀日侍女", "name_en": "Eclipse Maid", "icon": "gargoyle",
        "description": "狼人阵营但不属于狼窝，知道狼队友；从第二晚起每晚吞噬一名好人的技能并当晚使用",
        "description_en": "Wolf camp but outside the pack — you know the wolves; from night two, devour a good player's skill and use it that night",
        "rules": [
            "属于狼人阵营，但**不属于狼窝**：不参与狼队夜间讨论与狼刀投票，不能自爆。你**知道**其他狼人是谁，其他狼人却不知道你。",
            "其他狼全部出局后，你获得带刀权，每晚可以独自刀人。",
            "**从第二晚开始**，每晚可以吞噬一名**好人阵营**玩家的技能：你当晚获得该技能并可立刻使用，被吞噬的玩家当晚失去技能直到天亮。",
            "你只在吞噬的那一夜拥有该技能；**不能连续两晚吞噬同一个人**。吞噬到狼人阵营的玩家只是落空。",
            "可吞噬的技能：预言家的查验、女巫的药（按对方当前剩余的药继承）、猎人的枪（触发条件不变）、守卫的守护、摄梦人的梦游、魔镜少女的身份查验、流光伯爵的庇护。",
            "**流光反噬**：吞噬流光伯爵，或吞噬当晚被流光伯爵庇护的玩家，你会在次日全体发言结束后出局（技能仍照常获得）。",
        ],
        "rules_en": [
            "Wolf camp but not in the pack: no wolf chat or kill vote, no self-destruct. You know who the other wolves are; they don't know you.",
            "Once all other wolves are out, you gain the kill and strike alone each night.",
            "From night two on, each night you may devour a good player's skill: you gain it that night and may use it at once, while the devoured player loses it until dawn.",
            "You hold the skill only for that night; never devour the same player two nights running. Devouring a wolf-camp player simply whiffs.",
            "Devourable skills: the Seer's check, the Witch's potions (whatever she has left), the Hunter's gun (same triggers), the Guard's shield, the Dream Weaver's dream, the Mirror Girl's exact-role check, the Radiance Earl's shelter.",
            "Radiance backlash: devour the Radiance Earl, or a player sheltered by him that night, and you die after the next day's speeches (you still gain the skill).",
        ],
    },
    "wolf-killer-time-wolf": {
        "name_zh": "蚀时狼妃", "name_en": "Time Wolf", "icon": "werewolf",
        "description": "狼人阵营，每晚率先睁眼封锁一名玩家，好人对其释放的验人、毒药、守护都落到自己身上",
        "description_en": "Werewolf; first to wake each night — locks a player in a time rift so good skills aimed at them hit their casters",
        "rules": [
            "属于狼人阵营：夜间与狼队共享讨论、频道与狼刀投票，与普通狼人一致。",
            "每晚**最先睁眼**，可以选择场上**任意一名玩家**（包括自己和狼队友）封锁到时间的夹缝中；可以不封锁，也可以连续两晚封锁同一个人。",
            "当晚好人阵营玩家对被封锁玩家释放的**验人、毒药和守护**，都视为对**自身**释放：预言家验到自己、女巫毒到自己、守卫守到自己。解药不受影响。",
            "被封锁的人不会被告知；好人只会发现自己的技能「打偏了」。",
            "**技能生效的下一夜，永久失去封锁技能。**封锁了却没有人对该玩家放技能，不算生效，次夜仍可使用。",
        ],
        "rules_en": [
            "Wolf camp: you share the pack's night chat, channel, and kill vote like a normal werewolf.",
            "You wake first each night and may lock any player (yourself or wolf teammates included) in a time rift; locking is optional, and the same player may be locked two nights running.",
            "That night, good players' checks, poisons, and shields aimed at the locked player land on themselves instead: the Seer checks herself, the Witch poisons herself, the Guard shields himself. The antidote is unaffected.",
            "The locked player isn't told; the good side just finds their skills went astray.",
            "The night after the lock actually works, you lose the skill forever. If nobody targeted the locked player it doesn't count — you may lock again the next night.",
        ],
    },
    "wolf-killer-order-prince": {
        "name_zh": "定序王子", "name_en": "Order Prince", "icon": "knight",
        "description": "好人神职，每个白天第一次放逐投票结束后可翻牌作废该次投票并重新投票，限一次",
        "description_en": "Good god role; once per game, after the day's first exile vote, flips to void it and vote again",
        "rules": [
            "属于好人阵营的**神职**，没有夜间技能。",
            "每个白天**第一次放逐投票结束后**、真正放逐之前，可以翻牌发动技能：**逆转时空，回到本次投票前重新投票**，本次投票的结果作废。",
            "翻牌后**公开身份**，并可在重新投票前**额外发言一次**；随后全体存活玩家重新投票，可投任意存活玩家。",
            "每局游戏限一次；平票后的补充投票不能再发动。可以先观望，等到更需要的那一天再用。",
            "重新投票若再次平票，本日无人被放逐。",
        ],
        "rules_en": [
            "A god of the good camp, with no night skill.",
            "After the day's first exile vote resolves but before the exile lands, you may flip your card: rewind time to before the vote and vote again — the result is voided.",
            "Flipping reveals your role and grants one extra speech before the re-vote; then all living players vote again on any living player.",
            "Once per game; it can't fire on a tie-break runoff. You may wait for a day it matters more.",
            "If the re-vote also ties, nobody is exiled that day.",
        ],
    },
    "wolf-killer-fool-seer": {
        "name_zh": "愚言家", "name_en": "Fool Seer", "icon": "seer",
        "description": "好人神职，查验与预言家相同，白天可以翻牌自曝",
        "description_en": "Good god role; checks like the Seer and may flip to self-reveal by day",
        "rules": [
            "属于好人阵营的**神职**。",
            "每晚可以查验一名其他存活玩家的**当前归属**：好人、狼人或第三方。",
            "白天每位玩家发言前，可以翻牌自曝一次，公开自己的身份。",
            "自曝后仍然存活，保留投票权，也仍然可以被放逐。",
        ],
        "rules_en": [
            "A god of the good camp.",
            "Each night, check another living player's current allegiance: good, werewolf, or third party.",
            "Before any player's daytime speech you may flip your card once and reveal your role.",
            "After revealing you stay alive, keep your vote, and can still be exiled.",
        ],
    },
    "wolf-killer-iron-villager": {
        "name_zh": "铁好人", "name_en": "Iron Villager", "icon": "villager",
        "description": "好人平民，能力与平民相同，白天可以翻牌自曝",
        "description_en": "Good villager; same abilities as a Villager, and may flip to self-reveal by day",
        "rules": [
            "属于好人阵营的**平民**，没有夜间技能。",
            "白天每位玩家发言前，可以翻牌自曝一次，公开自己的身份。",
            "自曝后仍然存活，保留投票权，也仍然可以被放逐。",
        ],
        "rules_en": [
            "A villager of the good camp, with no night skill.",
            "Before any player's daytime speech you may flip your card once and reveal your role.",
            "After revealing you stay alive, keep your vote, and can still be exiled.",
        ],
    },
    "wolf-killer-crow": {
        "name_zh": "乌鸦", "name_en": "Crow", "icon": "villager",
        "description": "好人神职，每晚诅咒一名玩家，使其在下一次放逐计票时多一票",
        "description_en": "Good god role; curses one player each night — they take an extra vote at the next exile tally",
        "rules": [
            "属于好人阵营的**神职**。",
            "每晚可以诅咒一名存活玩家，也可以诅咒自己，也可以放弃。",
            "不能连续两晚诅咒同一个人。",
            "诅咒只在**下一次放逐计票**时给该玩家多加一票，平票后的复投同样加一票；警长竞选票不加。",
            "放逐结果落账后诅咒消失。若白天在计票前被中断，诅咒留到下一次真正的放逐计票。",
        ],
        "rules_en": [
            "A god of the good camp.",
            "Each night you may curse a living player — yourself included — or skip.",
            "Never curse the same player two nights running.",
            "The curse adds one vote to that player at the next exile tally — including a tie-break re-vote; sheriff ballots don't count.",
            "The curse is spent once an exile resolves. If the day is interrupted before tallying, it carries to the next real exile vote.",
        ],
    },
    "wolf-killer-cupid": {
        "name_zh": "丘比特", "name_en": "Cupid", "icon": "guard",
        "description": "第三方，首夜可将两名玩家连为挚友；一人一狼时三人同为第三方",
        "description_en": "Third party; on night one may link two players as lovers — one good and one wolf makes all three third party",
        "rules": [
            "开局阵营是**第三方**，不属于神职、平民或狼窝。",
            "首夜、狼队讨论之前，可以把两名不同的存活玩家连为挚友，可以包括自己，也可以放弃。",
            "挚友当夜互相知道对方的座位，不知道对方身份；丘比特也不知道两人身份。",
            "两名挚友都是好人时，丘比特算好人；都是狼人时，丘比特算狼人；一人一狼时，丘比特和这两人组成第三方。",
            "场上还有第三方时，好人与狼人都不能获胜；第三方要其余所有人出局才获胜。",
            "一名挚友出局时，另一名存活挚友跟随出局，整局只发生一次，没有遗言，猎人不能因此开枪。",
        ],
        "rules_en": [
            "You start as third party — neither god, villager, nor pack.",
            "On night one, before the wolves confer, you may link two different living players as lovers — yourself may be one — or skip.",
            "The lovers learn each other's seats that night, not each other's roles; Cupid doesn't learn their roles either.",
            "Two good lovers make Cupid count as good; two wolves make Cupid count as a wolf; one of each and the three of you form a third party.",
            "While a third party survives, neither good nor wolves can win; the third party wins only when everyone else is out.",
            "When one lover dies, the surviving lover follows — once per game, no last words, and a Hunter can't shoot over it.",
        ],
    },
    "wolf-killer-admirer": {
        "name_zh": "暗恋者", "name_en": "Admirer", "icon": "villager",
        "description": "好人平民，首夜选择暗恋对象，只有胜负结算跟随对象",
        "description_en": "Good villager; on night one picks a crush — only the win/lose result follows them",
        "rules": [
            "属于好人阵营的**平民**，身份与人数判定永远按本牌算（预言家验它为好人）。",
            "首夜选择一名其他玩家作为暗恋对象，也可以放弃。放弃后仍是平民。",
            "**胜负结算**跟随暗恋对象：对象所在阵营获胜，暗恋者才算获胜；对象落败，暗恋者一起落败。对象是否已经出局不影响跟随。",
            "暗恋者不会因此变成狼人：只有自己的胜负归属改变，不计入狼人存活人数；狼人屠民时仍然必须把暗恋者算进去。",
            "暗恋对象和其余玩家都不知道这次选择。",
        ],
        "rules_en": [
            "A villager of the good camp; identity and head counts always treat you as this card (the Seer reads you as good).",
            "On night one, pick another player as your crush, or skip — skipping keeps you a plain villager.",
            "Win or lose follows the crush: you win only if the crush's camp wins, and lose when it loses — whether the crush is still alive doesn't matter.",
            "You never become a werewolf: only your win condition changes, you don't count toward living wolves, and a villager-edge victory must still count you in.",
            "Neither the crush nor anyone else learns of the choice.",
        ],
    },
    "wolf-killer-old-drunkard": {
        "name_zh": "老酒鬼", "name_en": "Old Drunkard", "icon": "old_drunkard",
        "description": "平民，免疫魅惑；被毒或枪杀延到次日发言结束后死亡",
        "description_en": "Villager; immune to charm — poison and gunshots kill him only after the next day's speeches",
        "rules": [
            "属于平民（不计入神职），免疫狼美人的魅惑。",
            "被女巫毒杀或被猎人枪杀时不当场死亡：留下「中毒 / 中枪」标记，于次日全体发言结束后、放逐投票前死亡。",
            "其他出局方式（狼刀、放逐、自爆、被决斗或殉情带走）都立即死亡。",
        ],
        "rules_en": [
            "A villager (not a god), immune to the Wolf Beauty's charm.",
            "Witch poison and the Hunter's shot don't kill on the spot: a 'poisoned / shot' mark remains, and death lands after the next day's speeches, before the exile vote.",
            "Every other way out (wolf kill, exile, self-destruct, duel, love death) kills at once.",
        ],
    },
    "wolf-killer-awakened-wolf-beauty": {
        "name_zh": "觉醒狼美人", "name_en": "Awakened Wolf Beauty", "icon": "wolf_beauty",
        "description": "狼人，每隔一晚魅惑一名替身；首次面临出局时由替身代其出局，全局生效一次",
        "description_en": "Werewolf; charms a stand-in every other night — the first time she would die, the stand-in dies instead (once per game)",
        "rules": [
            "属于狼人阵营，夜晚与所有狼队友共用同一次狼刀、共同讨论并投票；**不能自爆**，也不能被狼队选为刀口。",
            "每隔一晚可以单独发动一次「挽歌幻象」魅惑一名玩家（不能是自己）作为**替身**。",
            "整局游戏中**第一次面临出局**时，被魅惑的玩家**替代**你出局，你活下来；此后技能永久失去"
            "（第二次受到伤害或被放逐会直接出局）。",
            "替身的出局是**直接出局**：守卫、摄梦人的守护都拦不住。夜间被出局时不翻牌；"
            "被放逐时先翻牌、再由替身出局，替身正常发表遗言。",
            "替身已经出局、或你还没发动过技能时面临出局，没有人会替代你。",
            "**被夜间出局的觉醒猎人「巡猎」击中时技能无效**，不产生替身。",
        ],
        "rules_en": [
            "Wolf camp: share the pack's kill, chat, and vote at night; cannot self-destruct, and the pack can't pick you as the target.",
            "Every other night you may cast an elegy illusion alone, charming a player (not yourself) as your stand-in.",
            "The first time you would be eliminated, the charmed player dies in your place and you survive; the skill is lost forever after (a second hit or exile takes you out directly).",
            "The stand-in's death is direct: neither the Guard nor the Dream Weaver can stop it. At night you don't flip; on exile you flip first, then the stand-in dies and gives last words normally.",
            "If the stand-in is already dead, or you have never charmed one, nobody substitutes for you.",
            "A Sweep from an Awakened Hunter who died at night ignores the skill — no stand-in is produced.",
        ],
    },
    "wolf-killer-awakened-seer": {
        "name_zh": "觉醒预言家", "name_en": "Awakened Seer", "icon": "seer",
        "description": "每晚窥视任意两名玩家，只得知其中是否有狼人",
        "description_en": "Each night, peer at any two players and learn only whether a wolf is among them",
        "rules": [
            "属于好人阵营、计入**神职**。",
            "每晚选择**任意两名存活玩家**（不能选自己），得知的唯一信息是：**这两人中是否有狼人**（不告诉你是哪一个，也不给具体身份）。",
            "可以连续多晚查验同一对，也可以每晚换人；同一夜只能查一次。",
            "结果只有你自己知道，是否公开由你决定。",
        ],
        "rules_en": [
            "Good camp, counted as a god.",
            "Each night pick any two living players (not yourself); the only thing you learn is whether a werewolf is among them — not which one, and no exact roles.",
            "You may check the same pair nights in a row or change every night; only one check per night.",
            "Only you see the result; going public is your call.",
        ],
    },
    "wolf-killer-fragrance-phantom": {
        "name_zh": "寻香魅影", "name_en": "Fragrance Phantom", "icon": "werewolf",
        "description": "狼人但不入狼队；首夜知晓一名其他狼人，每晚绑两人，一方出局另一方随之出局（一局一次）",
        "description_en": "Werewolf outside the pack; learns one wolf's seat on night one, binds two players each night — if one dies, the other follows (once per game)",
        "rules": [
            "属于狼人阵营，但**开局不入狼队**：不参与夜间狼队讨论与狼刀投票，狼队友也不知道你是谁，**不能自爆**。",
            "**首夜随机知晓一名其他狼人的位置**（只知道座位，不知道具体是谁的牌）。",
            "当除你之外的所有狼人全部出局后，你才可以独自刀人。",
            "每晚可以**任意绑定两名存活玩家**（可以绑自己、也可以绑狼队友）；新的绑定会覆盖上一次。",
            "当被绑定的两人中一人出局时，**另一人立刻随之出局**（死因 `bond_death`），无论前者因何出局。",
            "这个连带效果**整局只触发一次**：触发后技能永久失去，之后的夜晚不再能绑定。",
        ],
        "rules_en": [
            "Wolf camp but outside the pack: no wolf chat or kill vote, the wolves don't know who you are, and you cannot self-destruct.",
            "On night one you learn the seat of one other werewolf at random — the seat only, not which card.",
            "Only after every other wolf is out do you gain the solo kill.",
            "Each night you may bind any two living players (yourself or wolf teammates included); a new bind overwrites the last.",
            "When one of the bound pair dies, the other dies immediately (cause `bond_death`), whatever killed the first.",
            "The chain fires only once per game: after it triggers, the skill is gone and no more binds.",
        ],
    },
    "wolf-killer-pure-white": {
        "name_zh": "纯白之女", "name_en": "Pure White", "icon": "pure_white",
        "description": "好人神职，每晚查验一名玩家的具体身份，第二夜起验中狼人即当夜处决",
        "description_en": "Good god role; checks one player's exact role each night — from night two, a wolf found is executed that night",
        "rules": [
            "每晚查验一名存活玩家的**具体身份**（不是阵营，而是预言家 / 女巫 / 平民 / 狼人这样的角色），结果只有自己可见。",
            "从**第二夜**起，查验到狼人则该狼人当夜出局（死因「验杀」）；查验到好人不会造成死亡。",
            "第一夜只查验、不处决。",
            "被狼巫查验到时，你会在对方验中你的那一夜出局（双方同夜互验则同归于尽）。",
            "没有自保能力，也没有其他证明手段：验出的信息只能靠白天发言传递。",
        ],
        "rules_en": [
            "Each night, check one living player's exact role (not just alignment — Seer / Witch / Villager / Werewolf); only you see the result.",
            "From night two on, a werewolf you check dies that night (cause: 'killed by verdict'); checking a good player kills no one.",
            "Night one is check-only — no executions.",
            "If the Wolf Witch checks you, you die the night she finds you (checking each other the same night takes you both down).",
            "No self-protection and no way to prove findings: what you learn travels only through daytime speeches.",
        ],
    },
    "wolf-killer-mask": {
        "name_zh": "假面", "name_en": "Mask", "icon": "mask",
        "description": "狼人（假面舞会板），免疫女巫毒药；第二夜起可给一人戴面具，改变其在舞池中的阵营",
        "description_en": "Werewolf (Masquerade board); immune to the Witch's poison — from night two, may mask a player to flip their camp inside the dance pool",
        "rules": [
            "属于狼人阵营，夜晚与所有狼队友共用同一次狼刀、共同讨论并投票；**免疫女巫的毒药**。",
            "从**第二夜**起，在舞者之后单独行动：给一名存活玩家戴上面具，并同时得知对方**是否正在舞池中**（这个答案只有你知道）。",
            "面具只作用于**当晚舞池的阵营比较**：被戴面具的人在比较中被算作对立方，因此可以取消裁决（让整个舞池看起来同一阵营）或把少数方出局名额转移给另一个人。",
            "面具**不改变真实阵营**：不影响屠边判胜、预言家查验、狼刀与任何其他按阵营判定的规则。",
            "不能连续两晚给同一个人戴面具。",
        ],
        "rules_en": [
            "Wolf camp: share the pack's kill, chat, and vote at night; immune to the Witch's poison.",
            "From night two on, act alone after the Dancer: put a mask on a living player and learn whether they are in that night's dance pool (only you see the answer).",
            "The mask only affects that night's dance-pool alignment check: the masked player counts as the opposite side, which can void the verdict (make the pool look unanimous) or shift the minority's death onto someone else.",
            "The mask doesn't change real alignment: edge victory, the Seer's checks, the wolf kill, and every other alignment rule are unaffected.",
            "Never mask the same player two nights running.",
        ],
    },
    "wolf-killer-dancer": {
        "name_zh": "舞者", "name_en": "Dancer", "icon": "dancer",
        "description": "好人神职（假面舞会板），第二夜起每晚指定三人共舞，人数少的一方出局；免疫女巫毒药",
        "description_en": "Good god role (Masquerade board); from night two, picks three dancers each night — the smaller camp dies; immune to the Witch's poison",
        "rules": [
            "**免疫女巫的毒药**；狼刀、猎枪、放逐等其他出局方式照常生效。",
            "从**第二夜**起，每晚必须邀请恰好**三名**存活玩家共舞，不可放弃。",
            "结算时按三人的**真实阵营**比较：同阵营则无事发生；不同阵营则**人数少的一方**当夜出局（2 比 1 时那 1 人出局）。",
            "每名玩家整局只能参加一次共舞，被邀请过的人不会再进入可选名单；可选的存活玩家不足三人时起舞自动失效。",
            "如果你**自己**在舞池里，这三人当夜免疫狼刀（但挡不住女巫的毒药）。",
            "舞池的阵营比较会被「假面」的面具翻转：面具能让一名成员在本次比较中算作对立方，从而取消裁决或把出局名额转移给另一个人。",
        ],
        "rules_en": [
            "Immune to the Witch's poison; the wolf kill, Hunter's shot, exile, and other deaths work normally.",
            "From night two on, you must invite exactly three living players to dance each night — skipping is not allowed.",
            "At settlement the trio's real alignments are compared: same camp, nothing happens; mixed, the smaller side dies that night (at 2-to-1 the one dies).",
            "Each player can join the dance only once per game; invitees leave the pool, and with fewer than three eligible living players the dance fails on its own.",
            "If you are in the pool yourself, the three are immune to the wolf kill that night (but not to the Witch's poison).",
            "The Mask can flip the pool's comparison: a masked member counts as the opposite side, voiding the verdict or shifting the death onto someone else.",
        ],
    },
    "wolf-killer-nightmare": {
        "name_zh": "噩梦之影", "name_en": "Nightmare", "icon": "nightmare",
        "description": "狼人，在所有夜间行动之前恐惧一人使其当夜无法行动；恐惧到狼人则整队当夜不能袭击",
        "description_en": "Werewolf; first to act each night — fears one player out of using skills; fearing a wolf silences the whole pack",
        "rules": [
            "属于狼人阵营，夜晚与所有狼队友共用同一次狼刀、共同讨论并投票。",
            "每夜**最先行动**：恐惧一名存活玩家，使其**当夜无法使用任何技能**（守卫不能守、预言家不能验、女巫不能用药等）。",
            "不能连续两晚恐惧同一名玩家；可以恐惧自己或狼队友。",
            "**若恐惧到狼人，则整个狼人阵营当夜无法发动袭击** —— 对好人来说那一夜是平安夜，所以选错对象代价很大。",
            "没有自爆能力；首夜与其他狼不互知身份的差异在本作中按普通狼处理。",
        ],
        "rules_en": [
            "Wolf camp: share the pack's kill, chat, and vote at night.",
            "Act first each night: fear one living player, sealing all their skills that night (the Guard can't shield, the Seer can't check, the Witch can't dose, etc.).",
            "Never fear the same player two nights running; you may fear yourself or a wolf teammate.",
            "If the feared player is a werewolf, the whole wolf camp can't attack that night — a peaceful night for the good side, so a bad pick is costly.",
            "No self-destruct; the official night-one anonymity quirk is treated as a normal wolf here.",
        ],
    },
    "wolf-killer-crimson-envoy": {
        "name_zh": "赤月使徒", "name_en": "Crimson Envoy", "icon": "crimson_envoy",
        "description": "狼人，白天可自爆封印当晚所有好人技能；作为最后一个狼被放逐时可延到次日才出局",
        "description_en": "Werewolf; may self-destruct by day to seal all good skills that night — as the last wolf exiled, lingers until the next day",
        "rules": [
            "属于狼人阵营，夜晚与所有狼队友共用同一次狼刀、共同讨论并投票。",
            "白天每位玩家开口发言前可以自爆一次：当日剩余发言与投票全部取消，直接进入夜晚，**且当晚所有存活好人的技能被封印**（守卫不能守、预言家不能验、女巫不能用药、猎人不能开枪等）。",
            "自爆不带走任何人（与白狼王不同），也不翻牌；一局一次，发言结束后不能再发动。",
            "**最后一个狼被放逐时**：你可以存活到次日天亮之后，直到次日全体发言结束才出局 —— 狼队因此多出这一夜的行动机会；你在死亡前不会再参与投票。",
            "被毒杀、被枪杀等非放逐方式出局时不发动延迟。",
        ],
        "rules_en": [
            "Wolf camp: share the pack's kill, chat, and vote at night.",
            "Once per day, before any player speaks, you may self-destruct: the day's remaining speeches and vote are cancelled, play goes to night, and every living good player's skills are sealed that night (the Guard can't shield, the Seer can't check, the Witch can't dose, the Hunter can't shoot, etc.).",
            "The self-destruct takes no one (unlike the White Wolf King) and doesn't flip your card; once per game, and not after speeches end.",
            "Exiled as the last wolf: you survive past dawn until the next day's speeches end — buying the pack one more night of action; you don't vote again before dying.",
            "Poison, gunshots, and other non-exile deaths don't trigger the delay.",
        ],
    },
    "wolf-killer-demon-hunter": {
        "name_zh": "猎魔人", "name_en": "Demon Hunter", "icon": "demon_hunter",
        "description": "好人神职，第二夜起每晚狩猎一人，结果是狼则对方次日出局，是好人则自己次日出局",
        "description_en": "Good god role; from night two, hunts one player each night — a wolf dies the next day, a good target kills you instead",
        "rules": [
            "从**第二晚**开始，每晚可以狩猎一名存活玩家（也可以不狩猎）；同一个人可以连续两晚被狩猎。",
            "狩猎结果在**次日**发言结束后结算：对方是狼人则该玩家出局；对方是好人则**猎魔人自己**出局。",
            "夜间看不到阵营结果，只能靠白天发言判断，所以贸然开猎可能把自己带走。",
            "**女巫的毒药对你无效**；狼刀、猎枪、放逐等其他出局方式照常生效。",
            "属于神职，计入屠边判定的神职一侧。",
        ],
        "rules_en": [
            "From night two on, you may hunt one living player each night (hunting is optional); the same player may be hunted two nights running.",
            "The hunt settles after the next day's speeches: a werewolf target dies; a good target kills the Demon Hunter instead.",
            "You see no result at night — only daytime speeches guide you, so a reckless hunt can take you down.",
            "The Witch's poison can't hurt you; the wolf kill, Hunter's shot, exile, and other deaths work normally.",
            "A god — counted on the god side for edge victory.",
        ],
    },
    "wolf-killer-wolf-witch": {
        "name_zh": "狼巫", "name_en": "Wolf Witch", "icon": "wolf_witch",
        "description": "狼人，每晚查验一名非狼阵营玩家的具体身份，第二夜起验中纯白之女即当夜处决",
        "description_en": "Werewolf; checks one non-wolf player's exact role each night — from night two, finding the Pure White executes her that night",
        "rules": [
            "属于狼人阵营，夜晚与所有狼队友共用同一次狼刀、共同讨论并投票；不会自爆。",
            "每晚可以查验一名**狼人阵营之外**的存活玩家的具体身份，不能查自己，也不能查狼队友（查验队友会被判为无效）。",
            "从**第二夜**起，验到纯白之女则该纯白之女当夜出局（死因「验杀」）；验到其他好人不会造成死亡。",
            "第一夜只查验、不处决；查验结果只有你自己可见。",
            "与纯白之女同夜互验时双方同归于尽。",
        ],
        "rules_en": [
            "Wolf camp: share the pack's kill, chat, and vote at night; no self-destruct.",
            "Each night, check the exact role of one living player outside the wolf camp — not yourself, not wolf teammates (checking a teammate is invalid).",
            "From night two on, finding the Pure White kills her that night (cause: 'killed by verdict'); checking anyone else kills no one.",
            "Night one is check-only; only you see the results.",
            "If you and the Pure White check each other the same night, you both die.",
        ],
    },
}

STANDARD_PRESETS: list[dict] = [
    {
        "id": "nine-player-standard",
        "name": "九人标准场",
        "name_en": "9-Player Standard",
        "description": "3狼 3民 1预言家 1女巫 1猎人",
        "description_en": "3 Wolves, 3 Villagers, 1 Seer, 1 Witch, 1 Hunter",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-villager": 3,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
        },
        "enable_sheriff": False,
        "recommended_timeout_seconds": 3600,
    },
    {
        "id": "ten-player-standard",
        "name": "十人标准场",
        "name_en": "10-Player Standard",
        "description": "3狼 3民 1预言家 1女巫 1猎人 1守卫",
        "description_en": "3 Wolves, 3 Villagers, 1 Seer, 1 Witch, 1 Hunter, 1 Guard",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-villager": 3,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        },
        "enable_sheriff": False,
        "recommended_timeout_seconds": 3600,
    },
    {
        "id": "ten-player-speed",
        "name": "十人速推场",
        "name_en": "10-Player Speed",
        "description": "3狼 4民 1预言家 1女巫 1猎人",
        "description_en": "3 Wolves, 4 Villagers, 1 Seer, 1 Witch, 1 Hunter",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "victory": "edge",
        "recommended_timeout_seconds": 3600,
    },
    {
        "id": "six-player-standard",
        "name": "六人标准场",
        "name_en": "6-Player Standard",
        "description": "2狼 2民 1预言家 1猎人",
        "description_en": "2 Wolves, 2 Villagers, 1 Seer, 1 Hunter",
        "role_counts": {
            "wolf-killer-werewolf": 2,
            "wolf-killer-villager": 2,
            "wolf-killer-seer": 1,
            "wolf-killer-hunter": 1,
        },
        "enable_sheriff": False,
        "victory": "city",
        "recommended_timeout_seconds": 3600,
    },
    {
        "id": "twelve-player-idiot",
        "name": "十二人预女猎白",
        "name_en": "12-Player Seer Witch Hunter Idiot",
        "description": "4狼 4民 1预言家 1女巫 1猎人 1白痴",
        "description_en": "4 Wolves, 4 Villagers, 1 Seer, 1 Witch, 1 Hunter, 1 Idiot",
        "role_counts": {
            "wolf-killer-werewolf": 4,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-idiot": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        "id": "twelve-player-wolf-king",
        "name": "十二人白狼王",
        "name_en": "12-Player White Wolf King",
        "description": "3狼 1白狼王 4民 1预言家 1女巫 1猎人 1守卫",
        "description_en": "3 Wolves, 1 White Wolf King, 4 Villagers, 1 Seer, 1 Witch, 1 Hunter, 1 Guard",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-werewolf-king": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        "id": "twelve-player-knight",
        "name": "十二人骑士",
        "name_en": "12-Player Knight",
        "description": "4狼 4民 1预言家 1女巫 1猎人 1骑士",
        "description_en": "4 Wolves, 4 Villagers, 1 Seer, 1 Witch, 1 Hunter, 1 Knight",
        "role_counts": {
            "wolf-killer-werewolf": 4,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-knight": 1,
        },
        "enable_sheriff": False,
        "recommended_timeout_seconds": 7200,
    },
    {
        "id": "twelve-player-wolf-beauty",
        "name": "十二人狼美人",
        "name_en": "12-Player Wolf Beauty",
        "description": "3狼 1狼美人 3民 1老酒鬼 1预言家 1女巫 1猎人 1守卫",
        "description_en": "3 Wolves, 1 Wolf Beauty, 3 Villagers, 1 Old Drunkard, 1 Seer, 1 Witch, 1 Hunter, 1 Guard",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-wolf-beauty": 1,
            "wolf-killer-villager": 3,
            "wolf-killer-old-drunkard": 1,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        },
        "enable_sheriff": True,
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Wolf Beauty + Knight" tournament board.
        # Its rule set differs from the Jiamian boards above on two points: the
        # witch may never save herself, and a duel by the Knight takes nobody
        # along (``roles/wolf_beauty.py`` drops ``knight_duel`` from the revenge
        # reasons for every board, which is safe because Knight and Wolf Beauty
        # never share a Jiamian preset).
        "id": "twelve-player-wolf-beauty-knight",
        "name": "十二人狼美人骑士",
        "name_en": "12-Player Wolf Beauty & Knight",
        "description": "3狼 1狼美人 4民 1预言家 1女巫 1守卫 1骑士",
        "description_en": "3 Wolves, 1 Wolf Beauty, 4 Villagers, 1 Seer, 1 Witch, 1 Guard, 1 Knight",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-wolf-beauty": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-guard": 1,
            "wolf-killer-knight": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Wolf King + Guard" tournament board.
        "id": "twelve-player-wolf-king-guard",
        "name": "十二人狼王守卫",
        "name_en": "12-Player Wolf King & Guard",
        "description": "3狼 1狼王 4民 1预言家 1女巫 1猎人 1守卫",
        "description_en": "3 Wolves, 1 Wolf King, 4 Villagers, 1 Seer, 1 Witch, 1 Hunter, 1 Guard",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-wolf-king": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Evil Knight" tournament board.
        "id": "twelve-player-evil-knight",
        "name": "十二人恶夜骑士",
        "name_en": "12-Player Evil Knight",
        "description": "3狼 1恶夜骑士 4民 1预言家 1女巫 1猎人 1守卫",
        "description_en": "3 Wolves, 1 Evil Knight, 4 Villagers, 1 Seer, 1 Witch, 1 Hunter, 1 Guard",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-evil-knight": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Wolf King + Dream Weaver" board. No
        # Guard: the dream weave replaces the nightly shield.
        "id": "twelve-player-wolf-king-dream",
        "name": "十二人狼王摄梦人",
        "name_en": "12-Player Wolf King & Dream Weaver",
        "description": "3狼 1狼王 4民 1预言家 1女巫 1猎人 1摄梦人",
        "description_en": "3 Wolves, 1 Wolf King, 4 Villagers, 1 Seer, 1 Witch, 1 Hunter, 1 Dream Weaver",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-wolf-king": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-dream-weaver": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # 京城大师赛原创板（非网易）：舞池的少数方裁决 + 假面的面具翻转。
        # 女巫保持默认的首夜可自救 —— 这条规则覆盖只属于网易官方板。
        "id": "twelve-player-masquerade",
        "name": "十二人假面舞会",
        "name_en": "12-Player Masquerade",
        "description": "3狼 1假面 4民 1预言家 1女巫 1白痴 1舞者",
        "description_en": "3 Wolves, 1 Mask, 4 Villagers, 1 Seer, 1 Witch, 1 Idiot, 1 Dancer",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-mask": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-idiot": 1,
            "wolf-killer-dancer": 1,
        },
        "enable_sheriff": True,
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Nightmare" board: the dream weave stands
        # in for the guard's shield.
        "id": "twelve-player-nightmare",
        "name": "十二人噩梦之影",
        "name_en": "12-Player Nightmare",
        "description": "3狼 1噩梦之影 4民 1预言家 1女巫 1猎人 1摄梦人",
        "description_en": "3 Wolves, 1 Nightmare, 4 Villagers, 1 Seer, 1 Witch, 1 Hunter, 1 Dream Weaver",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-nightmare": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-dream-weaver": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Crimson Demon Hunter" board: the envoy's
        # self-destruct and the hunter's delayed swing are the two clocks.
        "id": "twelve-player-crimson-hunter",
        "name": "十二人赤月猎魔人",
        "name_en": "12-Player Crimson Demon Hunter",
        "description": "3狼 1赤月使徒 4民 1预言家 1女巫 1猎魔人 1白痴",
        "description_en": "3 Wolves, 1 Crimson Envoy, 4 Villagers, 1 Seer, 1 Witch, 1 Demon Hunter, 1 Idiot",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-crimson-envoy": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-demon-hunter": 1,
            "wolf-killer-idiot": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Pure White Night Shadow" tournament
        # board: no Seer at all — the Pure White's exact-identity check and the
        # Wolf Witch's mirrored one are the board's information engine.
        "id": "twelve-player-pure-white",
        "name": "十二人纯白夜影",
        "name_en": "12-Player Pure White Night Shadow",
        "description": "3狼 1狼巫 4民 1守卫 1女巫 1猎人 1纯白之女",
        "description_en": "3 Wolves, 1 Wolf Witch, 4 Villagers, 1 Guard, 1 Witch, 1 Hunter, 1 Pure White",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-wolf-witch": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-guard": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-pure-white": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Gargoyle + Gravekeeper" board.
        "id": "twelve-player-gargoyle-gravekeeper",
        "name": "十二人石像鬼守墓人",
        "name_en": "12-Player Gargoyle & Gravekeeper",
        "description": "3狼 1石像鬼 4民 1预言家 1女巫 1猎人 1守墓人",
        "description_en": "3 Wolves, 1 Gargoyle, 4 Villagers, 1 Seer, 1 Witch, 1 Hunter, 1 Gravekeeper",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-gargoyle": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-gravekeeper": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Night Stars" board (七周年, 2024-10-27).
        "id": "twelve-player-night-stars",
        "name": "十二人暗夜星辰",
        "name_en": "12-Player Night Stars",
        "description": "3狼 1觉醒狼美人 4民 1预言家 1女巫 1守卫 1觉醒猎人",
        "description_en": "3 Wolves, 1 Awakened Wolf Beauty, 4 Villagers, 1 Seer, 1 Witch, 1 Guard, 1 Awakened Hunter",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-awakened-wolf-beauty": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-guard": 1,
            "wolf-killer-awakened-hunter": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Lonely Girl" board.
        "id": "twelve-player-lonely-girl",
        "name": "十二人孤独少女",
        "name_en": "12-Player Lonely Girl",
        "description": "4狼 3民 1孤独少女 1预言家 1女巫 1猎人 1守卫",
        "description_en": "4 Wolves, 3 Villagers, 1 Lonely Girl, 1 Seer, 1 Witch, 1 Hunter, 1 Guard",
        "role_counts": {
            "wolf-killer-werewolf": 4,
            "wolf-killer-villager": 3,
            "wolf-killer-lonely-girl": 1,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Fragrance and Fate" board.
        "id": "twelve-player-fragrance",
        "name": "十二人寻香识命",
        "name_en": "12-Player Fragrance & Fate",
        "description": "3狼 1寻香魅影 4民 1觉醒预言家 1女巫 1猎人 1守卫",
        "description_en": "3 Wolves, 1 Fragrance Phantom, 4 Villagers, 1 Awakened Seer, 1 Witch, 1 Hunter, 1 Guard",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-fragrance-phantom": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-awakened-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-guard": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 12-player "Miracle Merchant" board.
        "id": "twelve-player-miracle-merchant",
        "name": "十二人奇迹商人",
        "name_en": "12-Player Miracle Merchant",
        "description": "3狼 1狼王 4民 1预言家 1女巫 1守卫 1奇迹商人",
        "description_en": "3 Wolves, 1 Wolf King, 4 Villagers, 1 Seer, 1 Witch, 1 Guard, 1 Miracle Merchant",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-wolf-king": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-guard": 1,
            "wolf-killer-miracle-merchant": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's college-league "Mirror" board: the Awakened Hidden Wolf copies
        # a player, and the Mirror Girl reads the exact identity it shows.
        "id": "twelve-player-mirror",
        "name": "十二人镜隐迷踪",
        "name_en": "12-Player Mirror & Hidden Wolf",
        "description": "3狼 1觉醒隐狼 4民 1守卫 1女巫 1猎人 1魔镜少女",
        "description_en": "3 Wolves, 1 Awakened Hidden Wolf, 4 Villagers, 1 Guard, 1 Witch, 1 Hunter, 1 Mirror Girl",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-awakened-hidden-wolf": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-guard": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-mirror-girl": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's college-league "Sun Hunt" board: the Eclipse Maid devours a skill,
        # the Radiance Earl shelters a seat — and punishes anyone who devours it.
        "id": "twelve-player-sun-hunt",
        "name": "十二人猎日逐光",
        "name_en": "12-Player Sun Hunt",
        "description": "3狼 1蚀日侍女 4民 1预言家 1女巫 1摄梦人 1流光伯爵",
        "description_en": "3 Wolves, 1 Eclipse Maid, 4 Villagers, 1 Seer, 1 Witch, 1 Dream Weaver, 1 Radiance Earl",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-eclipse-maid": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-dream-weaver": 1,
            "wolf-killer-radiance-earl": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's college-league "Eternal Cycle" board: the Time Wolf redirects
        # good skills onto their casters, the Order Prince rewinds a vote.
        "id": "twelve-player-eternal-cycle",
        "name": "十二人永序之轮",
        "name_en": "12-Player Eternal Cycle",
        "description": "3狼 1蚀时狼妃 4民 1预言家 1守卫 1女巫 1定序王子",
        "description_en": "3 Wolves, 1 Time Wolf, 4 Villagers, 1 Seer, 1 Guard, 1 Witch, 1 Order Prince",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-time-wolf": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-guard": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-order-prince": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
    {
        "id": "twelve-player-fool-seer",
        "name": "十二人愚言家",
        "name_en": "12-Player Fool Seer",
        "description": "3狼 1狼王 3民 1愚言家 1铁好人 1猎人 1女巫 1守卫",
        "description_en": "3 Wolves, 1 Wolf King, 3 Villagers, 1 Fool Seer, 1 Iron Villager, 1 Hunter, 1 Witch, 1 Guard",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-wolf-king": 1,
            "wolf-killer-villager": 3,
            "wolf-killer-fool-seer": 1,
            "wolf-killer-iron-villager": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-guard": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "victory": "edge",
        "recommended_timeout_seconds": 7200,
    },
    {
        "id": "twelve-player-cupid",
        "name": "十二人丘比特奇缘",
        "name_en": "12-Player Cupid's Romance",
        "description": "1预言家 1女巫 1猎人 1白痴 1摄梦人 1乌鸦 1暗恋者 1噩梦之影 1狼王 1狼巫 1蚀时狼妃 1丘比特",
        "description_en": "1 Seer, 1 Witch, 1 Hunter, 1 Idiot, 1 Dream Weaver, 1 Crow, 1 Admirer, 1 Nightmare, 1 Wolf King, 1 Wolf Witch, 1 Time Wolf, 1 Cupid",
        "role_counts": {
            "wolf-killer-seer": 1,
            "wolf-killer-witch": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-idiot": 1,
            "wolf-killer-dream-weaver": 1,
            "wolf-killer-crow": 1,
            "wolf-killer-admirer": 1,
            "wolf-killer-nightmare": 1,
            "wolf-killer-wolf-king": 1,
            "wolf-killer-wolf-witch": 1,
            "wolf-killer-time-wolf": 1,
            "wolf-killer-cupid": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "victory": "city",
        "recommended_timeout_seconds": 7200,
    },
    {
        # NetEase's official 2024 "Awakened Witch" 12-player board: the wolf
        # side fields the Awakened Wolf King, the good side the Awakened Witch;
        # the witch can never save herself on this board.
        "id": "twelve-player-awakened-witch",
        "name": "十二人觉醒女巫",
        "name_en": "12-Player Awakened Witch",
        "description": "3狼 1觉醒狼王 4民 1预言家 1守卫 1猎人 1觉醒女巫",
        "description_en": "3 Wolves, 1 Awakened Wolf King, 4 Villagers, 1 Seer, 1 Guard, 1 Hunter, 1 Awakened Witch",
        "role_counts": {
            "wolf-killer-werewolf": 3,
            "wolf-killer-awakened-wolf-king": 1,
            "wolf-killer-villager": 4,
            "wolf-killer-seer": 1,
            "wolf-killer-guard": 1,
            "wolf-killer-hunter": 1,
            "wolf-killer-awakened-witch": 1,
        },
        "enable_sheriff": True,
        "witch_self_save": "never",
        "recommended_timeout_seconds": 7200,
    },
]

FIELD_CONSTRAINTS: dict[str, int] = {
    "min_players": 4,
    "max_players": 12,
    "min_werewolves": 1,
    "min_good": 1,
}


def _catalog_item(role_id: str, spec) -> dict:
    meta = ROLE_METADATA.get(role_id)
    if meta is None:
        meta = {}
    display = spec.display_name or role_id
    return {
        "role_id": role_id,
        "display_name": display,
        "name_zh": meta.get("name_zh", display),
        "name_en": meta.get("name_en"),
        "camp": spec.camp_id,
        "icon": meta.get("icon", "unknown"),
        "description": meta.get("description", ""),
        "description_en": meta.get("description_en"),
        "rules": [rule for rule in meta.get("rules", ()) if isinstance(rule, str)],
        "rules_en": [
            rule for rule in meta.get("rules_en", ()) if isinstance(rule, str)
        ] or None,
        "min_count": spec.min_count,
        "max_count": spec.max_count,
        "dependencies": sorted(spec.dependencies),
        "exclusions": sorted(spec.exclusions),
    }


def role_catalog(registry_snapshot) -> list[dict]:
    """Project the frozen registry snapshot into frontend-facing role items."""
    return [
        _catalog_item(role_id, spec)
        for role_id, spec in sorted(registry_snapshot.specs.items())
    ]
