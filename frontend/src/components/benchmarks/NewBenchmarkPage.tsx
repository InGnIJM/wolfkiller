import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Box, Button, Card, CardContent, Chip, Container, FormControl,
  InputLabel, MenuItem, Paper, Select, Stack, TextField, Typography,
} from '@mui/material';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import SaveOutlinedIcon from '@mui/icons-material/SaveOutlined';

import { fetchPresets } from '../../api/client';
import { useBenchmarkStore } from '../../store/benchmarkStore';
import { useModelConfigStore } from '../../store/modelConfigStore';
import type { BenchmarkCreateInput, BenchmarkMode, GamePreset } from '../../store/types';

function requestId(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `benchmark-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function NewBenchmarkPage() {
  const navigate = useNavigate();
  const { createDraft, actionPending, error } = useBenchmarkStore();
  const { configs, load: loadModels, loading: modelsLoading } = useModelConfigStore();
  const [presets, setPresets] = useState<GamePreset[]>([]);
  const [name, setName] = useState('模型对抗评测');
  const [mode, setMode] = useState<BenchmarkMode>('mixed_arena');
  const [presetId, setPresetId] = useState('');
  const [seed, setSeed] = useState(42);
  const [blocks, setBlocks] = useState(1);
  const [maxGames, setMaxGames] = useState<number | ''>('');
  const [concurrency, setConcurrency] = useState(1);
  const [timeout, setTimeoutValue] = useState(3600);
  const [modelIds, setModelIds] = useState<string[]>([]);
  const [baselineId, setBaselineId] = useState('');
  const [candidateId, setCandidateId] = useState('');
  const [catalogError, setCatalogError] = useState('');
  const [clientRequestId] = useState(requestId);

  useEffect(() => {
    void loadModels();
    let active = true;
    void fetchPresets().then((items) => {
      if (!active) return;
      setPresets(items);
      if (items[0]) setPresetId(items[0].id);
    }).catch((loadError) => {
      if (active) setCatalogError(loadError instanceof Error ? loadError.message : String(loadError));
    });
    return () => { active = false; };
  }, [loadModels]);

  const preset = presets.find((item) => item.id === presetId) ?? null;
  const playerCount = preset
    ? Object.values(preset.role_counts).reduce((total, count) => total + count, 0)
    : 0;
  const fullBlockGames = mode === 'mixed_arena' ? playerCount * playerCount : 2;
  const untruncatedGames = fullBlockGames * blocks;
  const actualGames = maxGames === '' ? untruncatedGames : Math.min(maxGames, untruncatedGames);
  const balanced = actualGames === untruncatedGames || actualGames % Math.max(fullBlockGames, 1) === 0;
  const canSubmit = Boolean(
    name.trim() && preset && actualGames > 0
    && (mode === 'mixed_arena'
      ? modelIds.length > 0
      : baselineId && candidateId && baselineId !== candidateId && actualGames % 2 === 0),
  );

  const modelById = useMemo(() => new Map(configs.map((item) => [item.id, item])), [configs]);

  const submit = async () => {
    if (!preset || !canSubmit) return;
    const common = {
      client_request_id: clientRequestId,
      name: name.trim(), mode, seed,
      scenario: { scenario_id: preset.id, role_counts: preset.role_counts },
      block_count: blocks,
      concurrency,
      game_timeout_seconds: timeout,
      ...(maxGames === '' ? {} : { max_games: maxGames }),
    };
    const input: BenchmarkCreateInput = mode === 'mixed_arena'
      ? { ...common, games: actualGames, models: modelIds.map((id) => ({ model_config_id: id })) }
      : {
          ...common,
          repetitions: actualGames / 2,
          baseline: { model_config_id: baselineId },
          candidate: { model_config_id: candidateId },
        };
    const run = await createDraft(input);
    if (run) navigate(`/benchmarks/${run.run_id}`);
  };

  return (
    <Container component="main" maxWidth="md" sx={{ py: { xs: 2, sm: 4 }, px: { xs: 2, sm: 3 } }}>
      <Button startIcon={<ArrowBackIcon />} onClick={() => navigate('/benchmarks')} sx={{ mb: 2 }}>返回任务列表</Button>
      <Typography component="h1" variant="h4">新建评测草稿</Typography>
      <Typography color="text.secondary" sx={{ mt: 1, mb: 3 }}>
        创建只会冻结实验配置与完整排程；进入详情后点击“开始评测”才会调用模型。
      </Typography>
      {(error || catalogError) && <Alert severity="error" sx={{ mb: 2 }}>{error || catalogError}</Alert>}

      <Stack spacing={3}>
        <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
          <Typography component="h2" variant="h6" sx={{ mb: 2 }}>实验设置</Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
            <TextField label="任务名称" value={name} onChange={(event) => setName(event.target.value)} required />
            <FormControl>
              <InputLabel id="benchmark-mode-label">评测模式</InputLabel>
              <Select labelId="benchmark-mode-label" label="评测模式" value={mode} onChange={(event) => setMode(event.target.value as BenchmarkMode)}>
                <MenuItem value="mixed_arena">混合模型对抗</MenuItem>
                <MenuItem value="paired_regression">A/B 配置回归</MenuItem>
              </Select>
            </FormControl>
            <FormControl>
              <InputLabel id="scenario-label">角色场景</InputLabel>
              <Select labelId="scenario-label" label="角色场景" value={presetId} onChange={(event) => setPresetId(event.target.value)}>
                {presets.map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
              </Select>
            </FormControl>
            <TextField label="随机种子" type="number" value={seed} onChange={(event) => setSeed(Math.max(0, Number(event.target.value)))} slotProps={{ htmlInput: { min: 0 } }} />
            <TextField label="完整区组数" type="number" value={blocks} onChange={(event) => setBlocks(Math.max(1, Number(event.target.value)))} slotProps={{ htmlInput: { min: 1 } }} />
            <TextField label="最多局数（留空不截断）" type="number" value={maxGames} onChange={(event) => setMaxGames(event.target.value === '' ? '' : Math.max(1, Number(event.target.value)))} slotProps={{ htmlInput: { min: 1 } }} />
            <TextField label="并发数" type="number" value={concurrency} onChange={(event) => setConcurrency(Math.max(1, Math.min(4, Number(event.target.value))))} helperText="默认为 1，最多 4" slotProps={{ htmlInput: { min: 1, max: 4 } }} />
            <TextField label="单局活动超时（秒）" type="number" value={timeout} onChange={(event) => setTimeoutValue(Math.max(1, Number(event.target.value)))} helperText="暂停、排队与离线时间不计入" slotProps={{ htmlInput: { min: 1 } }} />
          </Box>
          {preset && (
            <Stack direction="row" useFlexGap spacing={1} sx={{ mt: 2, flexWrap: 'wrap' }} aria-label="角色配置">
              {Object.entries(preset.role_counts).map(([role, count]) => <Chip key={role} label={`${role} × ${count}`} />)}
            </Stack>
          )}
        </Paper>

        <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
          <Typography component="h2" variant="h6" sx={{ mb: 2 }}>模型配置</Typography>
          {mode === 'mixed_arena' ? (
            <FormControl fullWidth>
              <InputLabel id="mixed-models-label">参赛模型</InputLabel>
              <Select
                multiple labelId="mixed-models-label" label="参赛模型" value={modelIds}
                onChange={(event) => setModelIds(typeof event.target.value === 'string' ? event.target.value.split(',') : event.target.value)}
                renderValue={(selected) => selected.map((id) => modelById.get(id)?.name ?? id).join('、')}
              >
                {configs.filter((item) => !item.key_invalid).map((item) => <MenuItem key={item.id} value={item.id}>{item.name} · {item.model_id}</MenuItem>)}
              </Select>
            </FormControl>
          ) : (
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
              {([['基线配置', baselineId, setBaselineId], ['候选配置', candidateId, setCandidateId]] as const).map(([label, value, setter]) => (
                <TextField key={label} select label={label} value={value} onChange={(event) => setter(event.target.value)}>
                  {configs.filter((item) => !item.key_invalid).map((item) => <MenuItem key={item.id} value={item.id}>{item.name} · {item.model_id}</MenuItem>)}
                </TextField>
              ))}
            </Box>
          )}
          {!modelsLoading && configs.length === 0 && <Alert severity="warning" sx={{ mt: 2 }}>请先在模型管理中添加可用配置。</Alert>}
        </Paper>

        <Card component="section" variant="outlined" aria-live="polite">
          <CardContent>
            <Typography component="h2" variant="h6">排程预览</Typography>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', sm: 'repeat(4, 1fr)' }, gap: 2, mt: 2 }}>
              <Box><Typography color="text.secondary">玩家数</Typography><Typography variant="h5">{playerCount}</Typography></Box>
              <Box><Typography color="text.secondary">完整区组局数</Typography><Typography variant="h5">{fullBlockGames}</Typography></Box>
              <Box><Typography color="text.secondary">实际总局数</Typography><Typography variant="h5">{actualGames}</Typography></Box>
              <Box><Typography color="text.secondary">均衡状态</Typography><Chip color={balanced ? 'success' : 'warning'} label={balanced ? '完整均衡' : '截断，不完全均衡'} /></Box>
            </Box>
          </CardContent>
        </Card>

        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button variant="contained" size="large" startIcon={<SaveOutlinedIcon />} disabled={!canSubmit || actionPending} aria-busy={actionPending} onClick={() => void submit()}>
            {actionPending ? '正在创建…' : '创建草稿'}
          </Button>
        </Box>
      </Stack>
    </Container>
  );
}
