import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
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
import type {
  BenchmarkCreateInput, BenchmarkMode, GamePreset, ModelLatencyStats,
} from '../../store/types';
import { BACKDROP } from '../../theme/tokens';

/** 单局活动超时默认值（秒）：12 人板一局约 136 次模型调用，慢模型下 3600 秒必然超时。 */
const DEFAULT_TIMEOUT_SECONDS = 7200;

/** 预估单局时长的安全余量：覆盖长尾样本与后期变慢（存活人数减少后发言与复投拉长）。 */
const LATENCY_SAFETY_FACTOR = 1.3;

/**
 * 预估单局模型调用耗时（毫秒）。
 * 调用次数按实测拟合：10 人无警长约 111 次、12 人无警长约 117 次、12 人开警长约 136 次，
 * 公式 `人数 × 10 +（开警长 ? 16 : 0）` 对后两者误差约 ±10%。
 */
function estimateSingleGameMs(
  { playerCount, enableSheriff, avgMs }:
  { playerCount: number; enableSheriff: boolean; avgMs: number },
): number {
  const estimatedCalls = playerCount * 10 + (enableSheriff ? 16 : 0);
  return estimatedCalls * avgMs * LATENCY_SAFETY_FACTOR;
}

/** 预估值与单局超时的对比结论：超过超时即几乎必定超时，超过 75% 建议调大超时。 */
function timeoutEstimateAlert(
  estimatedMs: number, timeoutSeconds: number, t: TFunction,
): { severity: 'info' | 'warning' | 'error'; message: string } {
  const estimatedMinutes = Math.round(estimatedMs / 60000);
  const timeoutMinutes = Math.round(timeoutSeconds / 60);
  if (estimatedMs > timeoutSeconds * 1000) {
    return {
      severity: 'error',
      message: t('benchmark.estimateOver', { estimated: estimatedMinutes, timeout: timeoutMinutes }),
    };
  }
  if (estimatedMs > timeoutSeconds * 1000 * 0.75) {
    return {
      severity: 'warning',
      message: t('benchmark.estimateNear', { estimated: estimatedMinutes, timeout: timeoutMinutes }),
    };
  }
  return {
    severity: 'info',
    message: t('benchmark.estimateOk', { estimated: estimatedMinutes, timeout: timeoutMinutes }),
  };
}

function requestId(): string {
  return globalThis.crypto?.randomUUID?.()
    ?? `benchmark-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function NewBenchmarkPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { createDraft, actionPending, error } = useBenchmarkStore();
  const { configs, load: loadModels, loading: modelsLoading } = useModelConfigStore();
  const [presets, setPresets] = useState<GamePreset[]>([]);
  const [name, setName] = useState(() => t('benchmark.defaultName'));
  const [mode, setMode] = useState<BenchmarkMode>('mixed_arena');
  const [presetId, setPresetId] = useState('');
  const [seed, setSeed] = useState(42);
  const [blocks, setBlocks] = useState(1);
  const [maxGames, setMaxGames] = useState<number | ''>('');
  const [concurrency, setConcurrency] = useState(1);
  const [timeout, setTimeoutValue] = useState(DEFAULT_TIMEOUT_SECONDS);
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

  // 切换角色预设时按预设的推荐超时抬升单局超时，只升不降：用户手动调大的值不会被调小，
  // 而从轻板切到 12 人板时也不会停在不够用的旧值上。首次加载直接 setPresetId，
  // 不走这里，所以默认超时仍是 DEFAULT_TIMEOUT_SECONDS。
  const applyPreset = (nextPresetId: string) => {
    setPresetId(nextPresetId);
    const recommended = presets.find(
      (item) => item.id === nextPresetId,
    )?.recommended_timeout_seconds;
    if (typeof recommended !== 'number' || !Number.isInteger(recommended) || recommended <= 0) return;
    setTimeoutValue((current) => Math.max(current, recommended));
  };

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

  // 混座评测的发言是串行的，单局总时长由所选模型的平均延迟决定，因此多模型取算术平均而非最大值。
  const selectedModelIds = mode === 'mixed_arena' ? modelIds : [baselineId, candidateId].filter(Boolean);
  const latencySamples = selectedModelIds
    .map((id) => modelById.get(id)?.latency)
    .filter((item): item is ModelLatencyStats => item != null && item.samples > 0);
  const averageLatencyMs = latencySamples.length === 0
    ? null
    : latencySamples.reduce((total, item) => total + item.avg_ms, 0) / latencySamples.length;
  const estimate = preset && averageLatencyMs !== null
    ? timeoutEstimateAlert(
        estimateSingleGameMs({
          playerCount, enableSheriff: Boolean(preset.enable_sheriff), avgMs: averageLatencyMs,
        }),
        timeout, t,
      )
    : null;

  const submit = async () => {
    if (!preset || !canSubmit) return;
    const common = {
      client_request_id: clientRequestId,
      name: name.trim(), mode, seed,
      scenario: {
        scenario_id: preset.id,
        role_counts: preset.role_counts,
        enable_sheriff: Boolean(preset.enable_sheriff),
        // Frozen into the run, never re-read from the live catalog: a benchmark
        // must keep replaying the rule set it was created under.
        witch_self_save: preset.witch_self_save ?? 'first_night_only',
        victory: preset.victory ?? 'edge',
      },
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
    <Container component="main" maxWidth="md" sx={{
      position: 'relative', zIndex: 1,
      py: { xs: 2, sm: 4 }, px: { xs: 2, sm: 3 },
      bgcolor: BACKDROP.textPlate, backdropFilter: 'blur(12px)', borderRadius: 2,
    }}>
      <Button startIcon={<ArrowBackIcon />} onClick={() => navigate('/benchmarks')} sx={{ mb: 2 }}>{t('benchmark.backToList')}</Button>
      <Typography component="h1" variant="h4">{t('benchmark.newTitle')}</Typography>
      <Typography color="text.secondary" sx={{ mt: 1, mb: 3 }}>
        {t('benchmark.newIntro')}
      </Typography>
      {(error || catalogError) && <Alert severity="error" sx={{ mb: 2 }}>{error || catalogError}</Alert>}

      <Stack spacing={3}>
        <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
          <Typography component="h2" variant="h6" sx={{ mb: 2 }}>{t('benchmark.sectionExperiment')}</Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
            <TextField label={t('benchmark.fieldName')} value={name} onChange={(event) => setName(event.target.value)} required />
            <FormControl>
              <InputLabel id="benchmark-mode-label">{t('benchmark.fieldMode')}</InputLabel>
              <Select labelId="benchmark-mode-label" label={t('benchmark.fieldMode')} value={mode} onChange={(event) => setMode(event.target.value as BenchmarkMode)}>
                <MenuItem value="mixed_arena">{t('benchmark.modeMixedLong')}</MenuItem>
                <MenuItem value="paired_regression">{t('benchmark.modeRegressionLong')}</MenuItem>
              </Select>
            </FormControl>
            <FormControl>
              <InputLabel id="scenario-label">{t('benchmark.fieldScenario')}</InputLabel>
              <Select labelId="scenario-label" label={t('benchmark.fieldScenario')} value={presetId} onChange={(event) => applyPreset(event.target.value)}>
                {presets.map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
              </Select>
            </FormControl>
            <TextField label={t('benchmark.fieldSeed')} type="number" value={seed} onChange={(event) => setSeed(Math.max(0, Number(event.target.value)))} slotProps={{ htmlInput: { min: 0 } }} />
            <TextField label={t('benchmark.fieldBlocks')} type="number" value={blocks} onChange={(event) => setBlocks(Math.max(1, Number(event.target.value)))} slotProps={{ htmlInput: { min: 1 } }} />
            <TextField label={t('benchmark.fieldMaxGames')} type="number" value={maxGames} onChange={(event) => setMaxGames(event.target.value === '' ? '' : Math.max(1, Number(event.target.value)))} slotProps={{ htmlInput: { min: 1 } }} />
            <TextField label={t('benchmark.fieldConcurrency')} type="number" value={concurrency} onChange={(event) => setConcurrency(Math.max(1, Math.min(4, Number(event.target.value))))} helperText={t('benchmark.concurrencyHint')} slotProps={{ htmlInput: { min: 1, max: 4 } }} />
            <TextField label={t('benchmark.fieldTimeout')} type="number" value={timeout} onChange={(event) => setTimeoutValue(Math.max(1, Number(event.target.value)))} helperText={t('benchmark.timeoutHint')} slotProps={{ htmlInput: { min: 1 } }} />
          </Box>
          {preset && (
            <Stack direction="row" useFlexGap spacing={1} sx={{ mt: 2, flexWrap: 'wrap' }} aria-label={t('benchmark.rolesAria')}>
              {Object.entries(preset.role_counts).map(([role, count]) => <Chip key={role} label={`${role} × ${count}`} />)}
            </Stack>
          )}
        </Paper>

        <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
          <Typography component="h2" variant="h6" sx={{ mb: 2 }}>{t('benchmark.sectionModels')}</Typography>
          {mode === 'mixed_arena' ? (
            <FormControl fullWidth>
              <InputLabel id="mixed-models-label">{t('benchmark.fieldMixedModels')}</InputLabel>
              <Select
                multiple labelId="mixed-models-label" label={t('benchmark.fieldMixedModels')} value={modelIds}
                onChange={(event) => setModelIds(typeof event.target.value === 'string' ? event.target.value.split(',') : event.target.value)}
                renderValue={(selected) => selected.map((id) => modelById.get(id)?.name ?? id).join(t('game.listJoin'))}
              >
                {configs.filter((item) => !item.key_invalid).map((item) => <MenuItem key={item.id} value={item.id}>{item.name} · {item.model_id}</MenuItem>)}
              </Select>
            </FormControl>
          ) : (
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
              {([[t('benchmark.baseline'), baselineId, setBaselineId], [t('benchmark.candidate'), candidateId, setCandidateId]] as const).map(([label, value, setter]) => (
                <TextField key={label} select label={label} value={value} onChange={(event) => setter(event.target.value)}>
                  {configs.filter((item) => !item.key_invalid).map((item) => <MenuItem key={item.id} value={item.id}>{item.name} · {item.model_id}</MenuItem>)}
                </TextField>
              ))}
            </Box>
          )}
          {!modelsLoading && configs.length === 0 && <Alert severity="warning" sx={{ mt: 2 }}>{t('benchmark.needConfigs')}</Alert>}
        </Paper>

        <Card component="section" variant="outlined" aria-live="polite">
          <CardContent>
            <Typography component="h2" variant="h6">{t('benchmark.sectionSchedule')}</Typography>
            <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', sm: 'repeat(4, 1fr)' }, gap: 2, mt: 2 }}>
              <Box><Typography color="text.secondary">{t('benchmark.playerCount')}</Typography><Typography variant="h5">{playerCount}</Typography></Box>
              <Box><Typography color="text.secondary">{t('benchmark.fullBlockGames')}</Typography><Typography variant="h5">{fullBlockGames}</Typography></Box>
              <Box><Typography color="text.secondary">{t('benchmark.actualGames')}</Typography><Typography variant="h5">{actualGames}</Typography></Box>
              <Box><Typography color="text.secondary">{t('benchmark.balance')}</Typography><Chip color={balanced ? 'success' : 'warning'} label={t(balanced ? 'benchmark.balanced' : 'benchmark.truncated')} /></Box>
            </Box>
            {preset && (
              <Alert severity={estimate ? estimate.severity : 'info'} sx={{ mt: 2 }}>
                {estimate ? estimate.message : t('benchmark.noEstimate')}
              </Alert>
            )}
          </CardContent>
        </Card>

        <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
          <Button variant="contained" size="large" startIcon={<SaveOutlinedIcon />} disabled={!canSubmit || actionPending} aria-busy={actionPending} onClick={() => void submit()}>
            {t(actionPending ? 'benchmark.creating' : 'benchmark.createDraft')}
          </Button>
        </Box>
      </Stack>
    </Container>
  );
}
