import { useEffect, useState } from 'react';
import {
  Alert, Box, Button, CircularProgress, Container, FormControlLabel, Snackbar, Stack, Switch, Typography,
} from '@mui/material';

import { useAppSettingsStore } from '../../store/appSettingsStore';
import type { PromptCaptureSetting } from '../../store/types';
import { BACKDROP } from '../../theme/tokens';

/**
 * 开关行副标题随 `source` 三态变化。故意留在组件内：`labels.ts` 的 19 个命名空间按渲染语境划分，
 * 「应用设置」不属于其中任何一类（禁止本地 `*_LABELS` / `*_TAGS`，故用单数 `_LABEL`）。
 */
const SOURCE_LABEL: Record<PromptCaptureSetting['source'], string> = {
  ui: '由本页开关控制（已忽略环境变量 PROMPT_CAPTURE_ENABLED）',
  env: '由环境变量 PROMPT_CAPTURE_ENABLED 控制（后端启动时读取，改动需重启后端）',
  default: '默认开启（未做任何界面设置，环境变量也未设置）',
};

/** 说明区 7 条（计划 §A.5.2 逐字稿；`lead` 为加粗小标题，`body` 为正文）。 */
const CAPTURE_NOTE: ReadonlyArray<{ lead: string; body: string }> = [
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
];

export default function SettingsPage() {
  const { setting, loading, error, load, setEnabled } = useAppSettingsStore();
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    void load();
  }, [load]);

  const closeFeedback = () => setFeedback(null);

  const handleToggle = async (enabled: boolean) => {
    const next = await setEnabled(enabled);
    if (!next) return;
    setFeedback(enabled
      ? '已开启：从下一个新建的对局开始采集。'
      : '已关闭：新建的对局不再采集；正在进行的对局会在本局结束后停止。');
  };

  const handleFollowEnv = async () => {
    const next = await setEnabled(null);
    if (!next) return;
    setFeedback(`已恢复为跟随环境变量 PROMPT_CAPTURE_ENABLED（当前：${next.enabled ? '开' : '关'}）。`);
  };

  return (
    <Container maxWidth="md" sx={{
      position: 'relative', zIndex: 1, py: 4,
      bgcolor: BACKDROP.textPlate, backdropFilter: 'blur(12px)', borderRadius: 2,
    }}>
      <Typography variant="h4" sx={{ fontWeight: 400, mb: 3 }}>设置</Typography>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {loading ? (
        <Box sx={{ display: 'grid', placeItems: 'center', py: 6 }}>
          <CircularProgress size={28} aria-label="正在加载设置" />
        </Box>
      ) : setting && (
        <Box sx={{
          bgcolor: 'background.paper',
          border: '1px solid', borderColor: 'divider',
          borderRadius: 2, p: 2.5,
        }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 500 }}>
            训练语料采集（LLM Prompt 原文）
          </Typography>

          <FormControlLabel
            sx={{ mt: 1.5, ml: 0 }}
            control={(
              <Switch
                checked={setting.enabled}
                onChange={(event) => { void handleToggle(event.target.checked); }}
              />
            )}
            label="采集 LLM Prompt 原文"
          />
          <Typography variant="body2" color="text.secondary">
            状态：{setting.enabled ? '开' : '关'}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {SOURCE_LABEL[setting.source]}
          </Typography>

          <Box sx={{ mt: 2.5 }}>
            <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
              这个开关做什么
            </Typography>
            <Stack spacing={1.2}>
              {CAPTURE_NOTE.map((note, index) => (
                <Typography key={note.lead} variant="body2" component="p" sx={{ lineHeight: 1.75 }}>
                  <Box component="span" sx={{ fontWeight: 600 }}>{`${index + 1}. ${note.lead}`}</Box>
                  {' —— '}
                  {note.body}
                </Typography>
              ))}
            </Stack>
          </Box>

          {setting.source === 'ui' && (
            <Button
              variant="outlined"
              size="small"
              sx={{ mt: 2.5 }}
              onClick={() => { void handleFollowEnv(); }}
            >
              恢复为跟随环境变量
            </Button>
          )}
        </Box>
      )}

      <Snackbar
        open={feedback !== null}
        autoHideDuration={6000}
        onClose={closeFeedback}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity="success" variant="filled" onClose={closeFeedback} sx={{ width: '100%' }}>
          {feedback}
        </Alert>
      </Snackbar>
    </Container>
  );
}
