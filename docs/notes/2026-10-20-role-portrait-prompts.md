# 角色立绘统一生成提示词（40 角色）

日期：2026-10-20
配套文件：[40 角色提示词清单 CSV](role-portrait-prompts.csv)（含每个角色的完整中英文 prompt，可直接复制）

## 一、这套提示词怎么用

目标是让 **40 个角色看起来出自同一套设定集**，而不是 40 张风格各异的图。做法是：

1. **一份固定的风格模板**（下面第二节），40 张图逐字复用；
2. **每个角色只替换三块变量**：`角色主体`、`背景`、`强调色`；
3. **先出 1 张风格锚点图**，再用它作为后面 39 张的风格参考（`--sref` / 参考图 / IP-Adapter）；
4. 每张图同一机位、同一光向、同一调色、同一笔触精度；
5. 生成 2–3 张候选，选最接近锚点的一张，统一裁切、调色、压缩。

> 注意：这是“一张图一个角色”的系列提示词，不是把 40 个角色塞进一张群像。

## 二、统一风格模板（English，推荐给 Midjourney / Flux / SDXL）

把 `{{SUBJECT_EN}}`、`{{BACKGROUND_EN}}`、`{{ACCENT_EN}}` 替换成 CSV 里对应角色的内容；
其余部分 40 张完全不动。

```text
[SERIES STYLE] Dark gothic fantasy character portrait, cinematic digital oil painting, hyper-detailed painterly brushwork, dramatic chiaroscuro, deep charcoal-black environment, desaturated pale skin, blood-crimson (#8F1F30 / #C22E42) and antique-gold (#D4A853) accents, cold steel-blue rim light, warm amber key light from the upper left, volumetric haze, 85mm portrait lens, three-quarter view, chest-up, centered composition, face in the upper third, eyes to camera, subject fills about 70% of the frame, shallow depth of field, background falling into shadow, no text, no logo, no watermark, no frame, no border, 3:4 vertical.

[CHARACTER] {{SUBJECT_EN}}

[BACKGROUND] {{BACKGROUND_EN}}, out of focus, dark atmospheric depth, subtle {{ACCENT_EN}} accents.

[CONSISTENCY] Same series as the other 39 portraits: identical camera height, lens, lighting direction, color grading, painterly finish, and level of detail. Single subject only. Full-bleed portrait, 768x1024, sRGB, crisp facial features, clean hands, natural anatomy.

[NEGATIVE] text, letters, logo, watermark, signature, frame, border, card UI, multiple characters, crowd, anime, manga, cartoon, 3D render, CGI, plastic skin, oversaturated colors, neon, lens flare, modern clothing, modern firearms, official game art, existing copyrighted character, celebrity likeness, blurry face, deformed hands, extra fingers, extra limbs, asymmetrical eyes, low resolution, jpeg artifacts.
```

## 三、统一风格模板（中文，推荐给即梦 / 可灵 / 文心）

```text
【系列风格】暗黑哥特奇幻角色半身像，电影感数字油画，超精细笔触，强烈明暗对照，深炭黑环境，低饱和苍白肤色，血红（#8F1F30 / #C22E42）与古典金（#D4A853）点缀，冷钢蓝轮廓光，左上方暖琥珀主光，体积雾，85mm 人像镜头，四分之三侧身，胸像构图，居中，脸部位于画面上三分之一，眼睛看向镜头，人物约占画面 70%，浅景深，背景沉入阴影，无文字、无 logo、无水印、无边框、无卡牌 UI，3:4 竖构图。

【角色】{{SUBJECT_ZH}}

【背景】{{BACKGROUND_ZH}}，虚化，暗调，带 {{ACCENT_ZH}} 点缀。

【一致性】与同系列其余 39 张保持同一机位、同一镜头、同一光向、同一调色、同一笔触精度。只出现一个角色。满幅立绘，768×1024，sRGB，面部清晰，手部干净，人体结构自然。

【负面词】文字、字母、logo、水印、签名、边框、卡牌 UI、多个人物、人群、二次元、漫画、卡通、3D 渲染、CGI、塑料皮肤、过饱和、霓虹、镜头光晕、现代服装、现代枪械、官方游戏原画、已有版权角色、真人明星脸、脸部模糊、畸形手、多余手指、多余肢体、大小眼、低分辨率、JPEG 噪点。
```

## 四、40 角色变量表

这张表是“替换变量”的唯一来源；英文主体、英文背景和完整 prompt 在
[CSV](role-portrait-prompts.csv) 的对应列里。

| # | 角色 | role_id | 阵营 | 主体（中文） | 背景 | 强调色 | 文件名 |
| ---: | --- | --- | --- | --- | --- | --- | --- |
| 1 | 狼人 | `wolf-killer-werewolf` | 狼人阵营 | 30岁男性，银白乱发与狼耳，猩红瞳孔，苍白皮肤带血痕；破旧黑红兜帽斗篷，露出利爪；右手抬起，指尖滴血，凶狠低吼。 | 血月下的哥特城堡废墟，红雾与断壁 | 血红与骨白 | `role-werewolf.webp` |
| 2 | 白狼王 | `wolf-killer-werewolf-king` | 狼人阵营 | 40岁男性，白色狼首头盔与骨冠，冰蓝眼，白须；厚重白狼毛披风覆盖黑金铠甲；手持巨大骨爪权杖。 | 雪原上的血月王座，白骨与残破旗帜 | 骨白与暗金 | `role-werewolf-king.webp` |
| 3 | 狼王 | `wolf-killer-wolf-king` | 狼人阵营 | 40岁男性，黑发后梳，狼首金冠，金瞳，脸上刀疤；黑金重甲与披风；一只覆甲巨爪。 | 黑金王座厅，血月透过高窗 | 暗金与黑 | `role-wolf-king.webp` |
| 4 | 恶夜骑士 | `wolf-killer-evil-knight` | 狼人阵营 | 30岁男性，裂开的黑盔露出苍白下颌与红眼；黑钢板甲，暗红披风，甲缝渗血；手持缺口长剑。 | 燃烧的教堂废墟，黑烟与余烬 | 暗红与铁黑 | `role-evil-knight.webp` |
| 5 | 石像鬼 | `wolf-killer-gargoyle` | 狼人阵营 | 无性别石像生物，灰黑石质皮肤，裂纹中透出暗红微光，巨大石翼，犄角，空洞发光眼；蹲踞姿态。 | 哥特教堂尖顶，暴雨夜空与闪电 | 石灰与暗红 | `role-gargoyle.webp` |
| 6 | 狼美人 | `wolf-killer-wolf-beauty` | 狼人阵营 | 20多岁女性，黑色长卷发与狼耳，琥珀眼，红唇；深红丝绒长裙，金色蔷薇饰品，露出狼尾；指尖轻抚一朵玫瑰。 | 红玫瑰温室，血月透过玻璃 | 玫红与暗红 | `role-wolf-beauty.webp` |
| 7 | 觉醒狼美人 | `wolf-killer-awakened-wolf-beauty` | 狼人阵营 | 20多岁女性，银白长发，半透明幻影替身在她身后，红绸缠绕，镜面碎片悬浮；红金长裙。 | 镜厅，红月与烛火 | 银白与血红 | `role-awakened-wolf-beauty.webp` |
| 8 | 狼巫 | `wolf-killer-wolf-witch` | 狼人阵营 | 30岁女性，黑发编成辫，狼牙项链，脸上图腾彩绘，黄褐眼；骨杖，狼皮披肩，暗红长袍。 | 原始森林祭坛，篝火与狼影 | 骨白与暗红 | `role-wolf-witch.webp` |
| 9 | 赤月使徒 | `wolf-killer-crimson-envoy` | 狼人阵营 | 20多岁女性，红发，赤红瞳孔，苍白皮肤；深红祭袍，金色锁链缠绕手腕，胸口红月徽记；双手捧着一颗发光红月石。 | 红月祭坛，悬浮灰烬与血雾 | 赤红与金 | `role-crimson-envoy.webp` |
| 10 | 噩梦之影 | `wolf-killer-nightmare` | 狼人阵营 | 无性别黑影人形，边缘化作触手与烟雾，脸上只有一双惨白发光眼；破碎镜面环绕。 | 噩梦般的扭曲卧室，黑雾与倾斜的家具 | 墨黑与幽蓝 | `role-nightmare.webp` |
| 11 | 假面 | `wolf-killer-mask` | 狼人阵营 | 中性青年，半张脸戴华丽金黑面具，另半张脸苍白微笑；黑羽礼服，手持一张面具。 | 假面舞会大厅，吊灯与红帷幕 | 金与黑 | `role-mask.webp` |
| 12 | 寻香魅影 | `wolf-killer-fragrance-phantom` | 狼人阵营 | 20多岁女性，黑纱遮面，只露一双紫眼，长发；暗紫长裙，手持香炉，青烟化作狼影。 | 东方风格香阁，纱幔与月光 | 紫与青 | `role-fragrance-phantom.webp` |
| 13 | 觉醒隐狼 | `wolf-killer-awakened-hidden-wolf` | 狼人阵营 | 20多岁女性，短发，狼耳隐藏在半透明兜帽下，异色瞳；深灰刺客装，半脸面具，手持短刃。 | 雾夜暗巷，远处狼影 | 灰与暗红 | `role-awakened-hidden-wolf.webp` |
| 14 | 蚀日侍女 | `wolf-killer-eclipse-maid` | 狼人阵营 | 20多岁女性，黑发盘髻，日蚀状金环头饰，黑纱遮眼；黑白女仆式长裙，手持淬毒匕首，日蚀光晕。 | 日蚀下的宫殿长廊，冷光与长影 | 黑、金与日蚀白 | `role-eclipse-maid.webp` |
| 15 | 蚀时狼妃 | `wolf-killer-time-wolf` | 狼人阵营 | 30岁女性，银灰长发，狼耳，金瞳；黑金宫廷长裙，身后悬浮巨大时钟，指尖拨动沙漏。 | 时钟塔内部，巨大齿轮与时间光尘 | 金与银灰 | `role-time-wolf.webp` |
| 16 | 预言家 | `wolf-killer-seer` | 好人阵营·神职 | 30岁男性，银白长发，尖耳，蓝眼，苍白；黑紫兜帽长袍，金色星月刺绣，双手捧水晶球，球中狼影。 | 星空塔楼，血月与星图 | 星蓝与金 | `role-seer.webp` |
| 17 | 女巫 | `wolf-killer-witch` | 好人阵营·神职 | 30岁女性，黑发，紫眼，苍白；黑紫长裙，药瓶腰带，紫焰在掌心，另一手拿毒瓶。 | 药剂室，蒸馏瓶与紫烟 | 紫与毒绿 | `role-witch.webp` |
| 18 | 猎人 | `wolf-killer-hunter` | 好人阵营·神职 | 40岁男性，短发灰白，络腮胡，伤疤，琥珀眼；棕色皮甲，厚重猎装，手持古董燧发长管猎枪，腰间弹药。 | 雾林与篝火 | 琥珀与棕 | `role-hunter.webp` |
| 19 | 觉醒猎人 | `wolf-killer-awakened-hunter` | 好人阵营·神职 | 30岁男性，黑发，狼血涂面，红眼；黑色猎装，双持古董燧发短枪，银弹带。 | 血月猎场，狼影与枯树 | 银与血红 | `role-awakened-hunter.webp` |
| 20 | 守卫 | `wolf-killer-guard` | 好人阵营·神职 | 30岁男性，短黑发，青眼，方脸；青色重甲，巨大鸢盾，盾面青焰纹章；手持战锤。 | 城堡大门，青焰火盆 | 青与铁灰 | `role-guard.webp` |
| 21 | 白痴 | `wolf-killer-idiot` | 好人阵营·神职 | 20多岁男性，乱发，脸上画着泪滴与笑脸，戴破旧三叉铃帽；拼接小丑服，手持铃铛权杖，眼神清醒而悲哀。 | 废弃剧场，红幕与灰尘 | 暗金与红 | `role-idiot.webp` |
| 22 | 摄梦人 | `wolf-killer-dream-weaver` | 好人阵营·神职 | 20多岁女性，银蓝长发，半闭眼，睫毛结霜；浅蓝纱裙，周围悬浮梦蝶与月光丝线，手持捕梦网。 | 月下梦境花园，薄雾 | 月蓝与银 | `role-dream-weaver.webp` |
| 23 | 守墓人 | `wolf-killer-gravekeeper` | 好人阵营·神职 | 40岁男性，灰发，胡茬，疲惫眼；破旧黑斗篷，铁锹，提灯，腰间挂着墓碑钥匙。 | 雾夜墓园，歪斜墓碑与乌鸦 | 灰绿与灯火橙 | `role-gravekeeper.webp` |
| 24 | 骑士 | `wolf-killer-knight` | 好人阵营·神职 | 30岁男性，金褐短发，蓝眼，坚毅；银蓝板甲，白色披风，手持长剑，剑身泛蓝光。 | 哥特教堂大厅，彩窗月光 | 钢蓝与银 | `role-knight.webp` |
| 25 | 纯白之女 | `wolf-killer-pure-white` | 好人阵营·神职 | 20多岁女性，白金色长发，白瞳，苍白；纯白圣袍，银色护甲片，手持银剑，背后圣光。 | 白色教堂废墟，光柱与灰烬 | 纯白与银 | `role-pure-white.webp` |
| 26 | 猎魔人 | `wolf-killer-demon-hunter` | 好人阵营·神职 | 30岁男性，黑发，灰眼，脸上有旧伤；黑色长皮风衣，银色十字弩，银弹与符文，腰间挂着头骨。 | 雨夜哥特街道，路灯与雾 | 银与暗红 | `role-demon-hunter.webp` |
| 27 | 觉醒预言家 | `wolf-killer-awakened-seer` | 好人阵营·神职 | 20多岁女性，银白长发，双眼异色（蓝/金），瞳孔中有星图；深蓝星纹长袍，双手间悬浮双星与星盘。 | 观星台，双月与星云 | 星蓝与金 | `role-awakened-seer.webp` |
| 28 | 舞者 | `wolf-killer-dancer` | 好人阵营·神职 | 20多岁女性，红发盘起，红眼，笑意；深红舞裙，红舞鞋，手持半张面具，裙摆化作火焰与花瓣。 | 假面舞会舞池，红帷幕与吊灯 | 红与金 | `role-dancer.webp` |
| 29 | 奇迹商人 | `wolf-killer-miracle-merchant` | 好人阵营·神职 | 40岁男性，圆脸，精明眼，金牙，戴礼帽或兜帽；华丽商人外套，钱袋，金骰，货箱，手持天平。 | 夜市帐篷，金币与魔法光 | 金与绿 | `role-miracle-merchant.webp` |
| 30 | 魔镜少女 | `wolf-killer-mirror-girl` | 好人阵营·神职 | 16-18岁少女，黑长直，苍白，蓝眼；黑蓝哥特裙，手持古董手镜，镜中倒影与她动作不一致。 | 镜厅，蓝焰烛台 | 蓝与银 | `role-mirror-girl.webp` |
| 31 | 流光伯爵 | `wolf-killer-radiance-earl` | 好人阵营·神职 | 40岁男性，金发后梳，金瞳，优雅；黑金刺绣礼服，白色手套，手持酒杯，另一手悬浮光盾。 | 烛光宴会厅，金色光尘 | 金与暖白 | `role-radiance-earl.webp` |
| 32 | 定序王子 | `wolf-killer-order-prince` | 好人阵营·神职 | 20多岁男性，黑发，金瞳，年轻王子；深蓝金边礼服，手持卷轴与逆转沙漏，身后时间齿轮。 | 王座厅，倒流的光尘 | 深蓝与金 | `role-order-prince.webp` |
| 33 | 愚言家 | `wolf-killer-fool-seer` | 好人阵营·神职 | 30岁男性，乱发，一只眼戴单片眼镜或假眼，嘴角神秘微笑；小丑式学者长袍，手持提线木偶与放大镜。 | 堆满书的剧场后台，烛光 | 紫与金 | `role-fool-seer.webp` |
| 34 | 铁好人 | `wolf-killer-iron-villager` | 好人阵营·平民 | 30多岁男性，方脸，短须，眼神沉稳而坚定；粗布衬衫与旧皮背心，肩上搭着麻布，胸前挂着一枚无字的小铁牌；一手按在胸口，站姿笔直，像在当众表明身份。 | 黄昏的村庄广场，木屋与暖灯，远处麦田与炊烟 | 亚麻色、暖橙与一点铁灰 | `role-iron-villager.webp` |
| 35 | 乌鸦 | `wolf-killer-crow` | 好人阵营·神职 | 中性，30岁，黑发，黑眼，苍白；黑羽斗篷，肩上与指尖各停着一只乌鸦，周围鸦群。 | 枯树与血月，鸦群 | 墨黑与暗紫 | `role-crow.webp` |
| 36 | 丘比特 | `wolf-killer-cupid` | 第三方 | 18-20岁中性青年，金发，粉红眼，背生小型羽翼；古典白色与红色短袍，手持弓与玫瑰，腰间箭袋。 | 哥特花园，玫瑰与红月 | 玫红与金 | `role-cupid.webp` |
| 37 | 暗恋者 | `wolf-killer-admirer` | 好人阵营·平民 | 20多岁女性，深棕长发，羞怯眼，苍白；深色斗篷，手持未寄出的信与心形吊坠，半躲在阴影中。 | 昏暗闺房，烛光、信纸与未寄出的信 | 暗红与暖黄 | `role-admirer.webp` |
| 38 | 老酒鬼 | `wolf-killer-old-drunkard` | 好人阵营·平民 | 60岁男性，灰白乱发，红鼻，醉眼，胡茬；破旧棕褐斗篷与帽子，手持酒壶，坐在酒馆木桶旁。 | 昏暗酒馆，烛光与木桶 | 琥珀与暖橙 | `role-old-drunkard.webp` |
| 39 | 孤独少女 | `wolf-killer-lonely-girl` | 好人阵营·平民 | 16-18岁少女，黑色长发，苍白，眼神孤独；破旧深色洋装，抱膝坐在雨夜窗边，身旁有提灯与旧玩偶。 | 雨夜阁楼窗边，冷雨与微弱灯光 | 冷蓝与暖黄 | `role-lonely-girl.webp` |
| 40 | 村民 | `wolf-killer-villager` | 好人阵营·平民 | 30岁男性，粗布衣，草叉，提灯，疲惫但坚毅。 | 雾气弥漫的村庄边缘，木屋与田野 | 土绿与暖黄 | `role-villager.webp` |

**铁好人特别注意**：这里的“铁”是“铁证如山 / 铁好人”的**比喻**，不是铁匠。画面不要盔甲、盾牌、战锤、铁匠铺、铁砧、炉火、打铁动作；用粗布衣、无字小铁牌、手按胸口（自曝身份的姿态）和黄昏村庄来表达。CSV 的 `prompt_zh` / `prompt_en` 已在该行追加 `【本角色负面】`。

## 五、复制即用示例（狼人）

CSV 第一行就是完整英文 prompt；中文版如下（把其余角色的变量替换即可）：

```text
【系列风格】暗黑哥特奇幻角色半身像，电影感数字油画，超精细笔触，强烈明暗对照，深炭黑环境，低饱和苍白肤色，血红（#8F1F30 / #C22E42）与古典金（#D4A853）点缀，冷钢蓝轮廓光，左上方暖琥珀主光，体积雾，85mm 人像镜头，四分之三侧身，胸像构图，居中，脸部位于画面上三分之一，眼睛看向镜头，人物约占画面 70%，浅景深，背景沉入阴影，无文字、无 logo、无水印、无边框、无卡牌 UI，3:4 竖构图。
【角色】30岁男性，银白乱发与狼耳，猩红瞳孔，苍白皮肤带血痕；破旧黑红兜帽斗篷，露出利爪；右手抬起，指尖滴血，凶狠低吼。
【背景】血月下的哥特城堡废墟，红雾与断壁，虚化，暗调，带 血红与骨白 点缀。
【一致性】与同系列其余 39 张保持同一机位、同一镜头、同一光向、同一调色、同一笔触精度。只出现一个角色。满幅立绘，768×1024，sRGB，面部清晰，手部干净，人体结构自然。
【负面词】文字、字母、logo、水印、签名、边框、卡牌 UI、多个人物、人群、二次元、漫画、卡通、3D 渲染、CGI、塑料皮肤、过饱和、霓虹、镜头光晕、现代服装、现代枪械、官方游戏原画、已有版权角色、真人明星脸、脸部模糊、畸形手、多余手指、多余肢体、大小眼、低分辨率、JPEG 噪点。
```

## 六、按模型使用

### Midjourney（v6.1 / v7）

```text
<完整英文 prompt> --ar 3:4 --style raw --stylize 250 --v 6.1 --sref <风格锚点图 URL> --sw 100 --seed 20261020
```

- `--sref` 用第一步选出的风格锚点图；`--sw 100` 是风格权重，先 100，风格漂了再升到 150–200。
- `--no text, logo, watermark, frame, border, multiple characters`。
- 同一角色出 2–3 张，选最接近锚点的；不要每张换 seed。
- 如果 v7 对 `--sref` 支持不同，改用 v6.1 的 `--sref` 流程，或直接用参考图。

### Flux / SDXL / Stable Diffusion

- 分辨率：**768×1024**（3:4），不要用 1024×1024 再裁。
- 采样：DPM++ 2M Karras，28–40 steps，CFG 3.5–5.0（Flux 用 guidance 3.5 左右）。
- 固定 seed（例如 20261020）或同一 seed 族；固定 sampler/steps/CFG。
- 用 IP-Adapter / reference-only / style reference 指向风格锚点图，权重 0.6–0.8。
- 负面提示词直接用模板里的 `NEGATIVE`；SDXL 可把负面词放 negative prompt 框，Flux 可省略负面词但要在正向 prompt 里保留 “no text, no frame”。

### DALL·E 3 / GPT-image

- 不支持负面词和 seed，把模板改成一段自然语言，并在结尾加：
  `Do not include any text, letters, logos, watermarks, frames, borders, UI elements, multiple characters, or copyrighted characters.`
- 尺寸选 **1024×1536（2:3）**，生成后居中裁成 768×1024（3:4），保留头部空间。
- 一次只描述一个角色；不要让模型画“系列图”或“角色表”。

### 即梦 / 可灵 / 文心一格

- 直接用第三节中文模板 + 第四节变量。
- 比例选 3:4；风格参考图用锚点图，强度 0.6–0.8；不要开“自动增强”把暗部提亮。
- 负面词如果平台有独立输入框，直接粘贴中文负面词。

## 七、一致性工作流（最关键）

### 风格锚点 prompt（推荐，不进入最终 40 张）

先生成一张中性的“系列锚点”，只用来做 `--sref` / 参考图 / IP-Adapter。它不属于任何角色，
所以不会把狼耳、白头发之类的角色特征带进整套图。

```text
[STYLE ANCHOR] A neutral gothic fantasy character, androgynous adult in their 30s, pale skin, sharp features, dark high-collared coat with antique gold trim, standing in a dark stone hall, three-quarter view, chest-up, centered, face in the upper third, eyes to camera, warm amber key light from the upper left, cold steel-blue rim light from the right, volumetric haze, deep charcoal background, cinematic digital oil painting, hyper-detailed painterly brushwork, muted desaturated palette, 85mm lens, shallow depth of field, no text, no logo, no watermark, no frame, no border, 3:4 vertical.
```

> **不要用网易官方立绘做参考图**。锚点必须是自产图；否则风格参考会把官方画风/角色特征带回来，版权上也说不清。

1. **先出风格锚点**：用上面的锚点 prompt 生成 3–5 张；如果不想多一张，也可以直接用最终「狼人」或「预言家」图当锚点。选一张脸、光、色、笔触都满意的作为锚点。
2. **锁定参考**：后面 39 张全部带同一张锚点图（`--sref` / 参考图 / IP-Adapter）。不要每张重新找参考。
3. **锁定文本**：风格模板、负向词、机位、光向、调色描述一字不改；只换角色/背景/强调色。
4. **锁定参数**：同一模型、同一 sampler、同一 steps、同一 CFG、同一 seed 族；换角色不换参数。
5. **每个角色出 2–3 张**：选最像锚点的一张；明显跑偏的（脸型、年龄、画风、光向）直接弃。
6. **统一后期**：全部裁成 768×1024，眼睛保持在画面 30%–35% 高度，头顶留 8%–12%，肩部填满下缘；统一对比/饱和/暗部，输出 WebP q82。
7. **拼一张 contact sheet 检查**：40 张缩略图放一起看，重点查：
   - 是否“同一张脸”反复出现（同脸症）；
   - 手部/道具是否畸形；
   - 背景是否过亮、过杂，抢脸；
   - 是否有文字、签名、边框、水印；
   - 狼人阵营是否偏红、好人阵营是否偏冷，整体是否统一。

### 建议的首批样张

先跑 3 张再批量：**狼人**（红黑、兽性）、**预言家**（蓝金、神秘）、**女巫**（紫绿、药剂）。
这三张覆盖狼/神、暖/冷、男/女、实体/魔法，最容易暴露色偏、脸型跑偏、手部畸形和背景抢脸；
确认后再按同一锚点跑其余 37 张。

## 八、输出与入库

- 尺寸：**768×1024（3:4）**，WebP，质量 82 左右，sRGB，满幅无边框。
- 命名：`role-<slug>.webp`，`slug` = 角色 ID 去掉 `wolf-killer-`，**一律用连字符**。
- 完整 40 个角色里有 11 个没有登记（觉醒猎人、觉醒狼美人、觉醒预言家、寻香魅影、假面、舞者、愚言家、铁好人、乌鸦、丘比特、暗恋者）生成后要补
  `frontend/src/theme/tokens.ts` 的 `ROLE_ART_BY_ID`。如果当前检出只有 35 个角色，先补其中已接入的 6 个，剩下 5 个待接入后再补。
- 白狼王/狼美人的旧自制备份是下划线文件名，新图统一用连字符
  （`role-werewolf-king.webp` / `role-wolf-beauty.webp`），不要沿用旧名。
- 官方图仍在 `scripts/fetch-official-role-art.py` 里；全量替换后要退役该脚本或标注“仅本地研究”，并更新
  `THIRD_PARTY_NOTICES.md` 4.1/4.3。
- 新图要入库才能随发布包分发：当前 `.gitignore` 忽略 `assets/roles/role-*.webp`，需要先决定入库目录/忽略规则。
- 验收：`cd frontend && npm test && npm run build`；`theme.test.ts` 会检查所有登记角色都能取到图。

## 九、和盘点文档的关系

- 角色清单、已有/缺失状态、入库问题见 [角色立绘盘点](2026-10-20-role-portrait-inventory.md)。
- 本文件只负责“怎么生成”：统一风格、变量表、模型参数、一致性流程。
- 如果当前检出的角色目录是 35 个（愚言家等 5 个尚未接入），先按当前 35 个生成，剩下 5 个等并入后直接复用同一套模板补 5 张即可；提示词表已包含全部 40 个。
