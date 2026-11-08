import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Box, Button, Card, CardContent, Chip, Container, FormControl,
  FormControlLabel, IconButton, InputLabel, MenuItem, Paper, Select, Stack,
  Switch, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import ArrowBackIcon from '@mui/icons-material/ArrowBack';
import RemoveIcon from '@mui/icons-material/Remove';
import SaveOutlinedIcon from '@mui/icons-material/SaveOutlined';

import { fetchPresets } from '../../api/client';
import ModelVariantFields, {
  AssignmentQuantityControls,
} from '../models/variantFields';
import { useBenchmarkStore } from '../../store/benchmarkStore';
import { useModelConfigStore } from '../../store/modelConfigStore';
import type {
  BenchmarkCreateInput, BenchmarkMode, BenchmarkModelInput,
  GamePreset, ModelLatencyStats,
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

/** 模型变体草稿：config 固定供应商，其余字段都是当次选择的单次覆盖。 */
interface VariantDraft {
  key: string;
  configId: string;
  count: number;
  modelId: string | null;
  effort: string | null;
  /** 字符串态保留 ``0.`` 这类中间输入；空串表示继承配置存储值。 */
  temperature: string;
}

type VariantBase = Omit<VariantDraft, 'key' | 'count'>;

function blankVariant(count: number): VariantDraft {
  return {
    key: requestId(),
    configId: '',
    count,
    modelId: null,
    effort: null,
    temperature: '',
  };
}

function blankSide(): VariantBase {
  return { configId: '', modelId: null, effort: null, temperature: '' };
}

/** 温度留空 = 继承配置；填入时必须是 0–2 的数字。 */
function temperatureValid(raw: string): boolean {
  const trimmed = raw.trim();
  if (trimmed === '') return true;
  const value = Number(trimmed);
  return Number.isFinite(value) && value >= 0 && value <= 2;
}

function toModelInput(variant: VariantBase): BenchmarkModelInput {
  return {
    model_config_id: variant.configId,
    ...(variant.modelId !== null ? { model_id: variant.modelId } : {}),
    ...(variant.effort !== null ? { reasoning_effort: variant.effort } : {}),
    ...(variant.temperature.trim() !== ''
      ? { temperature: Number(variant.temperature.trim()) }
      : {}),
  };
}

function sameVariant(a: VariantBase, b: VariantBase): boolean {
  return JSON.stringify(toModelInput(a)) === JSON.stringify(toModelInput(b));
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
  const [rows, setRows] = useState<VariantDraft[]>([]);
  const [baseline, setBaseline] = useState<VariantBase>(blankSide);
  const [candidate, setCandidate] = useState<VariantBase>(blankSide);
  const [commentaryEnabled, setCommentaryEnabled] = useState(false);
  const [commentaryConfigId, setCommentaryConfigId] = useState<string | null>(null);
  const [commentaryModelId, setCommentaryModelId] = useState<string | null>(null);
  const [commentaryEffort, setCommentaryEffort] = useState<string | null>(null);
  const [voteConfigId, setVoteConfigId] = useState<string | null>(null);
  const [voteModelId, setVoteModelId] = useState<string | null>(null);
  const [voteEffort, setVoteEffort] = useState<string | null>(null);
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
    const next = presets.find((item) => item.id === nextPresetId);
    if (next) {
      const nextCount = Object.values(next.role_counts)
        .reduce((total, count) => total + count, 0);
      // 还没挑配置的单行直接跟随新人数，免得每次换板都手工重排。
      setRows((current) => (
        current.length === 1 && !current[0].configId
          ? [{ ...current[0], count: Math.max(1, nextCount) }]
          : current
      ));
    }
    const recommended = next?.recommended_timeout_seconds;
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

  const allocatedSeats = rows.reduce((total, row) => total + row.count, 0);
  const rowsValid = rows.length > 0
    && rows.every((row) => row.configId && row.count >= 1 && temperatureValid(row.temperature))
    && allocatedSeats === playerCount;
  const sidesPicked = Boolean(baseline.configId && candidate.configId);
  const identicalSides = sidesPicked && sameVariant(baseline, candidate);
  const sidesValid = sidesPicked && !identicalSides
    && temperatureValid(baseline.temperature)
    && temperatureValid(candidate.temperature);
  const canSubmit = Boolean(
    name.trim() && preset && actualGames > 0
    && (mode === 'mixed_arena' ? rowsValid : sidesValid && actualGames % 2 === 0),
  );

  const modelById = useMemo(() => new Map(configs.map((item) => [item.id, item])), [configs]);
  const usableConfigs = useMemo(
    () => configs.filter((item) => !item.key_invalid),
    [configs],
  );

  // 混座评测的发言是串行的，单局总时长由所选模型的平均延迟决定，因此多模型取算术平均而非最大值。
  const selectedConfigIds = mode === 'mixed_arena'
    ? [...new Set(rows.map((row) => row.configId).filter(Boolean))]
    : [baseline.configId, candidate.configId].filter(Boolean);
  const latencySamples = selectedConfigIds
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

  const patchRow = (index: number, patch: Partial<VariantDraft>) => {
    setRows((current) => current.map(
      (row, at) => (at === index ? { ...row, ...patch } : row),
    ));
  };

  const addRow = () => {
    setRows((current) => {
      const next = current.map((row) => ({ ...row }));
      const remaining = playerCount
        - next.reduce((total, row) => total + row.count, 0);
      let count: number;
      if (next.length === 0) {
        count = Math.max(1, playerCount);
      } else if (remaining >= 1) {
        count = remaining;
      } else {
        // 席位已分完：从最大行让出一席，新变体才有位置。
        const donor = next.reduce(
          (best, row, at) => (row.count > next[best].count ? at : best),
          0,
        );
        count = 1;
        if (next[donor].count > 1) next[donor].count -= 1;
      }
      next.push(blankVariant(count));
      return next;
    });
  };

  const removeRow = (index: number) => {
    setRows((current) => {
      const freed = current[index].count;
      const next = current.filter((_, at) => at !== index);
      // 释放的席位还给第一行，保持总席位与人数一致。
      if (next.length > 0) next[0] = { ...next[0], count: next[0].count + freed };
      return next;
    });
  };

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
      ...(commentaryEnabled || commentaryConfigId !== null
        ? {
            commentary_enabled: commentaryEnabled,
            commentary_config_id: commentaryConfigId,
            commentary_model_id: commentaryConfigId !== null ? commentaryModelId : null,
            commentary_reasoning_effort: commentaryConfigId !== null ? commentaryEffort : null,
          }
        : {}),
      ...(voteConfigId !== null
        ? {
            vote_model_config_id: voteConfigId,
            vote_model_id: voteModelId,
            vote_reasoning_effort: voteEffort,
          }
        : {}),
    };
    const input: BenchmarkCreateInput = mode === 'mixed_arena'
      ? {
          ...common,
          games: actualGames,
          model_assignments: rows.map((row) => ({
            ...toModelInput(row),
            count: row.count,
          })),
        }
      : {
          ...common,
          repetitions: actualGames / 2,
          baseline: toModelInput(baseline),
          candidate: toModelInput(candidate),
        };
    const run = await createDraft(input);
    if (run) navigate(`/benchmarks/${run.run_id}`);
  };

  const sideEditor = (
    label: string,
    ariaLabel: string,
    value: VariantBase,
    onChange: (patch: Partial<VariantBase>) => void,
  ) => (
    <Box
      role="group"
      aria-label={ariaLabel}
      sx={{ p: 1.5, border: '1px solid', borderColor: 'divider', borderRadius: 2 }}
    >
      <Stack spacing={1.5}>
        <Typography variant="subtitle2">{label}</Typography>
        <TextField
          select
          size="small"
          label={t('benchmark.variantConfig')}
          value={value.configId}
          onChange={(event) => onChange({ configId: event.target.value, modelId: null })}
          sx={{ minWidth: 200 }}
        >
          <MenuItem value="">{t('benchmark.selectConfig')}</MenuItem>
          {usableConfigs.map((item) => (
            <MenuItem key={item.id} value={item.id}>{item.name} · {item.model_id}</MenuItem>
          ))}
        </TextField>
        <ModelVariantFields
          config={modelById.get(value.configId)}
          modelId={value.modelId}
          effort={value.effort}
          temperature={value.temperature}
          temperatureError={!temperatureValid(value.temperature)}
          disabled={!value.configId}
          modelAria={t('benchmark.variantModelAria')}
          effortAria={t('benchmark.variantEffortAria')}
          temperatureAria={t('benchmark.variantTemperatureAria')}
          onModelIdChange={(modelId) => onChange({ modelId })}
          onEffortChange={(effort) => onChange({ effort })}
          onTemperatureChange={(temperature) => onChange({ temperature })}
        />
      </Stack>
    </Box>
  );

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
            <Stack spacing={1.5} role="group" aria-label={t('benchmark.fieldMixedModels')}>
              {rows.map((row, index) => {
                const rowName = modelById.get(row.configId)?.name
                  ?? t('benchmark.variantRowAria', { index: index + 1 });
                return (
                  <Box
                    key={row.key}
                    role="group"
                    aria-label={t('benchmark.variantRowAria', { index: index + 1 })}
                    sx={{ p: 1.5, border: '1px solid', borderColor: 'divider', borderRadius: 2 }}
                  >
                    <Stack spacing={1}>
                      <Stack
                        direction={{ xs: 'column', sm: 'row' }}
                        spacing={1}
                        useFlexGap
                        sx={{ alignItems: 'center', flexWrap: 'wrap' }}
                      >
                        <TextField
                          select
                          size="small"
                          label={t('benchmark.variantConfig')}
                          value={row.configId}
                          onChange={(event) => patchRow(index, {
                            configId: event.target.value,
                            modelId: null,
                          })}
                          sx={{ minWidth: 220, flex: 1 }}
                        >
                          <MenuItem value="">{t('benchmark.selectConfig')}</MenuItem>
                          {usableConfigs.map((item) => (
                            <MenuItem key={item.id} value={item.id}>{item.name} · {item.model_id}</MenuItem>
                          ))}
                        </TextField>
                        <AssignmentQuantityControls
                          name={rowName}
                          configId={row.configId || 'pending'}
                          count={row.count}
                          assignedTotal={allocatedSeats}
                          totalPlayers={Math.max(playerCount, 1)}
                          disabled={actionPending}
                          canIncrease
                          decreaseOneLabel={t('create.decreaseModel', { name: rowName })}
                          increaseOneLabel={t('create.increaseModel', { name: rowName })}
                          onChange={(nextCount) => patchRow(index, {
                            count: Math.max(1, nextCount),
                          })}
                        />
                        <IconButton
                          aria-label={t('benchmark.removeVariant')}
                          onClick={() => removeRow(index)}
                          sx={{ width: 40, height: 40 }}
                        >
                          <RemoveIcon />
                        </IconButton>
                      </Stack>
                      <ModelVariantFields
                        config={modelById.get(row.configId)}
                        modelId={row.modelId}
                        effort={row.effort}
                        temperature={row.temperature}
                        temperatureError={!temperatureValid(row.temperature)}
                        disabled={!row.configId || actionPending}
                        modelAria={t('benchmark.variantModelAria')}
                        effortAria={t('benchmark.variantEffortAria')}
                        temperatureAria={t('benchmark.variantTemperatureAria')}
                        onModelIdChange={(modelId) => patchRow(index, { modelId })}
                        onEffortChange={(effort) => patchRow(index, { effort })}
                        onTemperatureChange={(temperature) => patchRow(index, { temperature })}
                      />
                    </Stack>
                  </Box>
                );
              })}
              <Stack direction="row" spacing={2} useFlexGap sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
                <Button
                  variant="outlined"
                  size="small"
                  startIcon={<AddIcon />}
                  disabled={usableConfigs.length === 0 || actionPending}
                  onClick={addRow}
                >
                  {t('benchmark.addVariant')}
                </Button>
                <Typography
                  variant="body2"
                  color={allocatedSeats === playerCount ? 'text.secondary' : 'error.main'}
                >
                  {t('benchmark.seatsAllocated', { count: allocatedSeats, total: playerCount })}
                </Typography>
              </Stack>
            </Stack>
          ) : (
            <Stack spacing={2}>
              <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' }, gap: 2 }}>
                {sideEditor(
                  t('benchmark.baseline'),
                  t('benchmark.baselineRowAria'),
                  baseline,
                  (patch) => setBaseline((current) => ({ ...current, ...patch })),
                )}
                {sideEditor(
                  t('benchmark.candidate'),
                  t('benchmark.candidateRowAria'),
                  candidate,
                  (patch) => setCandidate((current) => ({ ...current, ...patch })),
                )}
              </Box>
              {identicalSides && (
                <Alert severity="warning">{t('benchmark.sameVariant')}</Alert>
              )}
            </Stack>
          )}
          {!modelsLoading && usableConfigs.length === 0 && <Alert severity="warning" sx={{ mt: 2 }}>{t('benchmark.needConfigs')}</Alert>}
        </Paper>

        <Paper component="section" variant="outlined" sx={{ p: { xs: 2, sm: 3 } }}>
          <Typography component="h2" variant="h6" sx={{ mb: 2 }}>{t('benchmark.auxModels')}</Typography>
          <Stack spacing={2}>
            <Box
              sx={{
                p: 2, borderRadius: 2, border: '1px solid',
                borderColor: 'divider', bgcolor: 'background.paper',
              }}
            >
              <Stack
                direction={{ xs: 'column', sm: 'row' }}
                spacing={2}
                sx={{ alignItems: { xs: 'flex-start', sm: 'center' }, justifyContent: 'space-between' }}
              >
                <Box>
                  <FormControlLabel
                    control={(
                      <Switch
                        checked={commentaryEnabled}
                        disabled={actionPending}
                        onChange={(event) => setCommentaryEnabled(event.target.checked)}
                      />
                    )}
                    label={t('create.commentaryToggle')}
                  />
                  <Typography variant="body2" color="text.secondary">
                    {t('create.commentaryHint')}
                  </Typography>
                </Box>
                {commentaryEnabled && (
                  <Select
                    size="small"
                    disabled={actionPending}
                    value={commentaryConfigId ?? ''}
                    displayEmpty
                    inputProps={{ 'aria-label': t('create.commentaryModelAria') }}
                    onChange={(event) => {
                      const value = event.target.value;
                      setCommentaryConfigId(value === '' ? null : value);
                      setCommentaryModelId(null);
                    }}
                    sx={{ minWidth: 220 }}
                  >
                    <MenuItem value="">{t('create.defaultModel')}</MenuItem>
                    {usableConfigs.map((item) => (
                      <MenuItem key={item.id} value={item.id}>{item.name} ({item.model_id})</MenuItem>
                    ))}
                  </Select>
                )}
              </Stack>
              {commentaryEnabled && commentaryConfigId && (
                <Box sx={{ mt: 2 }}>
                  <ModelVariantFields
                    config={modelById.get(commentaryConfigId)}
                    modelId={commentaryModelId}
                    effort={commentaryEffort}
                    disabled={actionPending}
                    modelAria={t('create.commentaryModelIdAria')}
                    effortAria={t('create.commentaryEffortAria')}
                    onModelIdChange={setCommentaryModelId}
                    onEffortChange={setCommentaryEffort}
                  />
                </Box>
              )}
            </Box>
            <Box
              sx={{
                p: 2, borderRadius: 2, border: '1px solid',
                borderColor: 'divider', bgcolor: 'background.paper',
              }}
            >
              <Stack
                direction={{ xs: 'column', sm: 'row' }}
                spacing={2}
                sx={{ alignItems: { xs: 'flex-start', sm: 'center' }, justifyContent: 'space-between' }}
              >
                <Box>
                  <Typography variant="body1">{t('create.voteModelLabel')}</Typography>
                  <Typography variant="body2" color="text.secondary">
                    {t('create.voteModelHint')}
                  </Typography>
                </Box>
                <Select
                  size="small"
                  disabled={actionPending}
                  value={voteConfigId ?? ''}
                  displayEmpty
                  inputProps={{ 'aria-label': t('create.voteModelAria') }}
                  onChange={(event) => {
                    const value = event.target.value;
                    setVoteConfigId(value === '' ? null : value);
                    setVoteModelId(null);
                  }}
                  sx={{ minWidth: 220 }}
                >
                  <MenuItem value="">{t('create.followSeatModel')}</MenuItem>
                  {usableConfigs.map((item) => (
                    <MenuItem key={item.id} value={item.id}>{item.name} ({item.model_id})</MenuItem>
                  ))}
                </Select>
              </Stack>
              {voteConfigId && (
                <Box sx={{ mt: 2 }}>
                  <ModelVariantFields
                    config={modelById.get(voteConfigId)}
                    modelId={voteModelId}
                    effort={voteEffort}
                    disabled={actionPending}
                    modelAria={t('create.voteModelIdAria')}
                    effortAria={t('create.voteEffortAria')}
                    onModelIdChange={setVoteModelId}
                    onEffortChange={setVoteEffort}
                  />
                </Box>
              )}
            </Box>
          </Stack>
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
