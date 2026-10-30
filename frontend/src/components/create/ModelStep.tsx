import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, Chip, FormControlLabel, IconButton, MenuItem, Select, Stack, Switch, TextField, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import RemoveIcon from '@mui/icons-material/Remove';

import ModelConfigDialog from '../models/ModelConfigDialog';
import { useModelConfigStore } from '../../store/modelConfigStore';
import type { ModelAssignment, ModelConfigInput } from '../../store/types';
import {
  nextAssignmentCount,
  parseAssignmentCount,
} from './assignmentQuantity';


interface Props {
  totalPlayers: number;
  assignments: ModelAssignment[];
  onAssignmentsChange: (assignments: ModelAssignment[]) => void;
  commentaryEnabled?: boolean;
  onCommentaryEnabledChange?: (enabled: boolean) => void;
  commentaryConfigId?: string | null;
  onCommentaryConfigIdChange?: (configId: string | null) => void;
  disabled?: boolean;
}

interface AssignmentRow {
  configId: string | null;
  name: string;
  detail: string;
  invalidReason: string | null;
}

function countFor(assignments: ModelAssignment[], configId: string | null): number {
  return assignments.find((entry) => entry.config_id === configId)?.count ?? 0;
}

const STEP_BUTTON_SX = {
  minWidth: 40,
  minHeight: 40,
  px: 0.75,
  fontVariantNumeric: 'tabular-nums',
  fontWeight: 600,
} as const;

function stepLabel(name: string, configId: string | null, delta: number, t: TFunction): string {
  const amount = Math.abs(delta);
  const verb = t(delta < 0 ? 'create.decreaseVerb' : 'create.increaseVerb');
  return configId === null
    ? t('create.adjustEnv', { verb, amount })
    : t('create.adjustModel', { name, verb, amount });
}

interface QuantityControlsProps {
  name: string;
  configId: string | null;
  count: number;
  assignedTotal: number;
  totalPlayers: number;
  disabled: boolean;
  canIncrease: boolean;
  decreaseOneLabel: string;
  increaseOneLabel: string;
  onChange: (nextCount: number) => void;
}

function AssignmentQuantityControls({
  name,
  configId,
  count,
  assignedTotal,
  totalPlayers,
  disabled,
  canIncrease,
  decreaseOneLabel,
  increaseOneLabel,
  onChange,
}: QuantityControlsProps) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(String(count));
  const focusedRef = useRef(false);

  useEffect(() => {
    if (!focusedRef.current) setDraft(String(count));
  }, [count]);

  const commitValue = (raw: string) => {
    const parsed = parseAssignmentCount(
      raw, count, assignedTotal, totalPlayers, canIncrease,
    );
    if (parsed === null) {
      setDraft(String(count));
      return;
    }
    setDraft(String(parsed));
    if (parsed !== count) onChange(parsed);
  };

  const applyDelta = (delta: number) => {
    onChange(nextAssignmentCount(count, delta, assignedTotal, totalPlayers, canIncrease));
  };

  const decreaseDisabled = disabled || count <= 0;
  const increaseDisabled = disabled
    || nextAssignmentCount(count, 1, assignedTotal, totalPlayers, canIncrease) === count;

  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: { xs: 'flex-start', sm: 'flex-end' },
        flexWrap: 'wrap',
        gap: 0.5,
      }}
    >
      {([-10, -5] as const).map((delta) => (
        <Button
          key={delta}
          type="button"
          size="small"
          variant="outlined"
          aria-label={stepLabel(name, configId, delta, t)}
          disabled={decreaseDisabled}
          onClick={() => applyDelta(delta)}
          sx={STEP_BUTTON_SX}
        >
          {delta}
        </Button>
      ))}
      <IconButton
        aria-label={decreaseOneLabel}
        disabled={decreaseDisabled}
        onClick={() => applyDelta(-1)}
        sx={{ width: 48, height: 48 }}
      >
        <RemoveIcon />
      </IconButton>
      <TextField
        size="small"
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
        onFocus={() => { focusedRef.current = true; }}
        onBlur={(event) => {
          focusedRef.current = false;
          commitValue(event.target.value);
        }}
        onKeyDown={(event) => {
          if (event.key !== 'Enter') return;
          event.preventDefault();
          commitValue((event.target as HTMLInputElement).value);
          (event.target as HTMLInputElement).blur();
        }}
        slotProps={{
          htmlInput: {
            inputMode: 'numeric',
            pattern: '[0-9]*',
            'aria-label': t('create.seatCountInput', { name }),
          },
        }}
        sx={{
          width: 64,
          '& .MuiInputBase-input': {
            textAlign: 'center',
            fontVariantNumeric: 'tabular-nums',
            fontWeight: 600,
            py: 1,
          },
        }}
      />
      <IconButton
        aria-label={increaseOneLabel}
        disabled={increaseDisabled}
        onClick={() => applyDelta(1)}
        sx={{ width: 48, height: 48 }}
      >
        <AddIcon />
      </IconButton>
      {([5, 10] as const).map((delta) => (
        <Button
          key={delta}
          type="button"
          size="small"
          variant="outlined"
          aria-label={stepLabel(name, configId, delta, t)}
          disabled={increaseDisabled}
          onClick={() => applyDelta(delta)}
          sx={STEP_BUTTON_SX}
        >
          +{delta}
        </Button>
      ))}
    </Box>
  );
}

export default function ModelStep({
  totalPlayers,
  assignments,
  onAssignmentsChange,
  commentaryEnabled = false,
  onCommentaryEnabledChange,
  commentaryConfigId = null,
  onCommentaryConfigIdChange,
  disabled = false,
}: Props) {
  const { t } = useTranslation();
  const {
    configs, loading, error, loadError, load, create,
  } = useModelConfigStore();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [focusConfigId, setFocusConfigId] = useState<string | null>(null);
  const focusRowRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!focusConfigId || dialogOpen || !focusRowRef.current) return undefined;
    focusRowRef.current.focus();
    setFocusConfigId(null);
    return undefined;
  }, [configs, dialogOpen, focusConfigId]);

  const configById = useMemo(
    () => new Map(configs.map((config) => [config.id, config])),
    [configs],
  );

  const rows = useMemo<AssignmentRow[]>(() => {
    const storedRows = configs.map((config) => ({
      configId: config.id,
      name: config.name,
      detail: `${config.model_id} · ${config.base_url}`,
      invalidReason: config.key_invalid
        ? t('create.keyInvalid')
        : loadError
          ? t('create.configUncertain')
          : null,
    }));
    const orphanRows = assignments
      .filter((entry) => entry.config_id !== null && !configById.has(entry.config_id))
      .map((entry) => ({
        configId: entry.config_id,
        name: t('create.deletedConfigName', { id: entry.config_id }),
        detail: t('create.deletedConfigDetail'),
        invalidReason: t('create.deletedConfigReason'),
      }));
    return [
      {
        configId: null,
        name: t('create.envDefaultName'),
        detail: t('create.envDefaultDetail'),
        invalidReason: null,
      },
      ...storedRows,
      ...orphanRows,
    ];
  }, [assignments, configById, configs, loadError, t]);

  const assignedTotal = assignments.reduce(
    (sum, entry) => sum + Math.max(0, entry.count),
    0,
  );
  const difference = totalPlayers - assignedTotal;

  const updateCount = (configId: string | null, nextCount: number) => {
    const count = Math.max(0, nextCount);
    const index = assignments.findIndex((entry) => entry.config_id === configId);
    if (index === -1) {
      onAssignmentsChange([...assignments, { config_id: configId, count }]);
      return;
    }
    onAssignmentsChange(assignments.map((entry, entryIndex) => (
      entryIndex === index ? { ...entry, count } : entry
    )));
  };

  const handleSaved = async (input: ModelConfigInput) => {
    const saved = await create(input);
    if (!saved) return;
    if (!assignments.some((entry) => entry.config_id === saved.id)) {
      onAssignmentsChange([...assignments, { config_id: saved.id, count: 0 }]);
    }
    setFocusConfigId(saved.id);
    setDialogOpen(false);
  };

  return (
    <Box>
      <Typography variant="subtitle2" sx={{ mb: 1 }}>
        {t('create.assignTitle', { count: totalPlayers })}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {t('create.assignHint')}
      </Typography>

      <Box
        role="status"
        aria-live="polite"
        sx={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 1,
          p: 1.5,
          mb: 2,
          border: '1px solid',
          borderColor: difference === 0 ? 'success.main' : 'warning.main',
          borderRadius: 2,
          bgcolor: 'background.paper',
        }}
      >
        <Typography variant="body1" sx={{ fontVariantNumeric: 'tabular-nums' }}>
          {t('create.assignedProgress', { assigned: assignedTotal, total: totalPlayers })}
        </Typography>
        <Chip
          size="small"
          color={difference === 0 ? 'success' : 'warning'}
          variant="outlined"
          label={
            difference === 0
              ? t('create.assignDone')
              : difference > 0
                ? t('create.assignRemaining', { count: difference })
                : t('create.assignOver', { count: Math.abs(difference) })
          }
        />
      </Box>

      {loading && (
        <Typography role="status" variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          {t('create.loadingModels')}
        </Typography>
      )}
      {error && (
        <Alert severity="warning" sx={{ mb: 1 }}>
          {error}. {t('create.modelsErrorSuffix')}
        </Alert>
      )}

      <Stack spacing={1}>
        {rows.map((row) => {
          const count = countFor(assignments, row.configId);
          const invalid = Boolean(row.invalidReason);
          const decreaseLabel = row.configId === null
            ? t('create.decreaseEnv')
            : t('create.decreaseModel', { name: row.name });
          const increaseLabel = row.configId === null
            ? t('create.increaseEnv')
            : t('create.increaseModel', { name: row.name });
          const isPendingFocus = row.configId !== null && row.configId === focusConfigId;
          return (
            <Box
              key={row.configId ?? 'environment-default'}
              ref={isPendingFocus ? focusRowRef : undefined}
              role="group"
              aria-label={t('create.assignInput', { name: row.name })}
              tabIndex={-1}
              sx={{
                display: 'grid',
                gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'minmax(0, 1fr) auto' },
                alignItems: 'center',
                gap: { xs: 1, sm: 2 },
                minWidth: 0,
                bgcolor: 'background.paper',
                border: '1px solid',
                borderColor: invalid && count > 0 ? 'warning.main' : 'divider',
                borderRadius: 2,
                p: 1.5,
                '&:focus-visible': {
                  outline: '2px solid',
                  outlineColor: 'secondary.main',
                  outlineOffset: 2,
                },
              }}
            >
              <Box sx={{ minWidth: 0 }}>
                <Stack
                  direction="row"
                  spacing={1}
                  useFlexGap
                  sx={{ alignItems: 'center', flexWrap: 'wrap' }}
                >
                  <Typography variant="body1">{row.name}</Typography>
                  {invalid && <Chip size="small" color="warning" label={t('create.unavailable')} />}
                </Stack>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ overflowWrap: 'anywhere' }}
                >
                  {row.detail}
                </Typography>
                {row.invalidReason && (
                  <Typography variant="body2" color="warning.main">
                    {row.invalidReason}
                  </Typography>
                )}
              </Box>

              <AssignmentQuantityControls
                name={row.name}
                configId={row.configId}
                count={count}
                assignedTotal={assignedTotal}
                totalPlayers={totalPlayers}
                disabled={disabled}
                canIncrease={
                  !invalid && !(row.configId !== null && loading)
                }
                decreaseOneLabel={decreaseLabel}
                increaseOneLabel={increaseLabel}
                onChange={(nextCount) => updateCount(row.configId, nextCount)}
              />
            </Box>
          );
        })}

        <Button
          variant="outlined"
          startIcon={<AddIcon />}
          disabled={disabled}
          onClick={() => setDialogOpen(true)}
          sx={{
            minHeight: 48,
            borderStyle: 'dashed',
            justifyContent: 'flex-start',
          }}
        >
          {t('create.createConfigInline')}
        </Button>

        {onCommentaryEnabledChange && (
          <Box
            sx={{
              mt: 2,
              p: 2,
              borderRadius: 2,
              border: '1px solid',
              borderColor: 'divider',
              bgcolor: 'background.paper',
            }}
          >
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={2}
              sx={{
                alignItems: { xs: 'flex-start', sm: 'center' },
                justifyContent: 'space-between',
              }}
            >
              <Box>
                <FormControlLabel
                  control={(
                    <Switch
                      checked={commentaryEnabled}
                      disabled={disabled}
                      onChange={(e) => onCommentaryEnabledChange(e.target.checked)}
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
                  disabled={disabled}
                  value={commentaryConfigId ?? ''}
                  displayEmpty
                  inputProps={{ 'aria-label': t('create.commentaryModelAria') }}
                  onChange={(e) => {
                    const val = e.target.value;
                    onCommentaryConfigIdChange?.(val === '' ? null : val);
                  }}
                  sx={{ minWidth: 220 }}
                >
                  <MenuItem value="">{t('create.defaultModel')}</MenuItem>
                  {configs
                    .filter((cfg) => !cfg.key_invalid)
                    .map((cfg) => (
                      <MenuItem key={cfg.id} value={cfg.id}>
                        {cfg.name} ({cfg.model_id})
                      </MenuItem>
                    ))}
                </Select>
              )}
            </Stack>
          </Box>
        )}
      </Stack>

      {dialogOpen && (
        <ModelConfigDialog
          open
          initial={null}
          onClose={() => setDialogOpen(false)}
          onSave={handleSaved}
        />
      )}
    </Box>
  );
}
