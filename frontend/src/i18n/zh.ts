/**
 * 简体中文资源树。结构与 `en.ts` 严格同形 —— `en.ts` 声明为 `typeof zh`，
 * tsc 会强制两语键值一一对应（注意：本文件不要对整棵树用 `as const`，
 * 否则 en 的值会被要求与 zh 逐字相同）。
 *
 * 机器值 → 文案的封闭表（死因、阶段、胜因等）不在此处：它们按既有约定留在
 * `src/labels.ts`（后端 `test_values.py` 用正则直接读它做覆盖门禁），
 * 英文镜像在 `src/i18n/labelsEn.ts`，由 `useLabels()` 按语言选取。
 */
export const zh = {
  common: {
    confirm: '确定',
    cancel: '取消',
    close: '关闭',
    save: '保存',
    delete: '删除',
    back: '返回',
    loading: '正在加载',
    unknown: '未知',
    seatNumber: '{{seat}}号',
    enabled: '开',
    disabled: '关',
    unknownRole: '未知身份',
  },
  nav: {
    subtitle: '血月剧场 · AI WEREWOLF',
    rules: '规则介绍',
    benchmarks: '模型评测',
    models: '模型管理',
    settings: '设置',
  },
  settings: {
    title: '设置',
    appearance: '外观',
    language: '语言',
    languageFollowSystem: '跟随系统',
    themeMode: '主题',
    themeFollowSystem: '跟随系统',
    themeDark: '深色',
    themeLight: '浅色',
    loadingAria: '正在加载设置',
    promptCapture: {
      title: '训练语料采集（LLM Prompt 原文）',
      switchLabel: '采集 LLM Prompt 原文',
      status: '状态：{{value}}',
      source: {
        ui: '由本页开关控制（已忽略环境变量 PROMPT_CAPTURE_ENABLED）',
        env: '由环境变量 PROMPT_CAPTURE_ENABLED 控制（后端启动时读取，改动需重启后端）',
        default: '默认开启（未做任何界面设置，环境变量也未设置）',
      },
      notesTitle: '这个开关做什么',
      notes: [
        {
          lead: '用途',
          body: '把每个座位实际发给模型的 prompt 原文（system + user 全文）和它的回复配成 (prompt, completion) 训练对，用于以后在本地微调小模型。它只写文件，不改变对局的任何逻辑。',
        },
        {
          lead: '写到哪',
          body: 'backend/data/games/<对局 ID>/llm_prompts.jsonl，一局一个文件，和 game.log、llm_calls.log 放在同一个目录。文件只追加，不覆盖、不自动清理。',
        },
        {
          lead: '占多大磁盘',
          body: '按本机已有 163 个对局实测（统计源：各局 llm_calls.log 的 prompt_chars）：典型一局约 2.3 MB（prompt 原文中位 142 万字符、约 116 条记录）；多数对局在 1.4 ~ 3.0 MB；调用次数多的长局可到 6 ~ 26 MB。磁盘紧张时请关闭本开关，或手动清理不再需要的 data/games/<id>/。',
        },
        {
          lead: '生效时机',
          body: '只对新建的对局生效。 已经在跑或已中断的对局按它开局时的设置跑完：开局时开着就采到本局结束，开局时关着就一直不采。这是故意的 —— 一局只采一半的语料是不完整的，训练时不好用。所以关掉开关后，正在进行的对局仍会继续写到本局结束。',
        },
        {
          lead: '关闭后有什么影响',
          body: '没有影响。发言、投票、夜间行动、断点续跑、回放全部照旧，只是不再产生新的采集文件（一个字节都不写）。',
        },
        {
          lead: '会不会写进密钥',
          body: '不会。落盘的只有 prompt 正文和少量元数据（轮次、阶段、座位、模型名、请求 ID）；API Key 与自定义请求头从不进入这些文件。',
        },
        {
          lead: '想立刻停掉某一局',
          body: '暂停该局 → 删除该局的 llm_prompts.jsonl → 恢复该局（恢复时不读开关，只看这个文件在不在）。注意这样该局只留下前半段语料；对局运行中无法删除该文件（句柄被占用），必须先暂停或中断。',
        },
      ],
      revertToEnv: '恢复为跟随环境变量',
      feedbackEnabled: '已开启：从下一个新建的对局开始采集。',
      feedbackDisabled: '已关闭：新建的对局不再采集；正在进行的对局会在本局结束后停止。',
      feedbackFollowEnv: '已恢复为跟随环境变量 PROMPT_CAPTURE_ENABLED（当前：{{value}}）。',
    },
    autoReview: {
      title: '赛后自动复盘（上帝视角）',
      switchLabel: '对局结束后自动生成赛后复盘',
      description: '普通对局结束后自动进行两阶段上帝视角复盘并写入 review.json（评测对局默认跳过自动生成，可在对局页手动点「赛后复盘」生成）。',
      feedbackEnabled: '已开启赛后自动复盘。',
      feedbackDisabled: '已关闭赛后自动复盘（仍可在对局页手动触发）。',
    },
  },
  errors: {
    invalidAudienceSeq: '收到无效的观众事件序号，已停止应用该事件。',
    seqConflict: '事件序号 {{seq}} 存在冲突，等待重新同步。',
    incompleteBenchmarkPage: '评测对局分页不完整，请重试。',
  },
};
