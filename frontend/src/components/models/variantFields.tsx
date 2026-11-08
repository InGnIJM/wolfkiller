import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useEffect, useRef, useState } from 'react';
import {
  Box, Button, IconButton, Select, Stack, TextField,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import RemoveIcon from '@mui/icons-material/Remove';

import type { ModelConfig } from '../../store/types';
import {
  nextAssignmentCount,
  parseAssignmentCount,
} from '../create/assignmentQuantity';
import {
  effortMenuItems,
  modelDefaultFor,
  modelListFor,
} from './variantUtils';

interface ModelVariantFieldsProps {
  config: ModelConfig | undefined;
  modelId: string | null;
  effort: string | null;
  /** 传入即显示温度输入；字符串态保留 ``0.`` 这类中间输入。 */
  temperature?: string;
  temperatureError?: boolean;
  disabled?: boolean;
  modelAria: string;
  effortAria: string;
  temperatureAria?: string;
  onModelIdChange: (value: string | null) => void;
  onEffortChange: (value: string | null) => void;
  onTemperatureChange?: (value: string) => void;
}

/**
 * 单次选择覆盖：model_id / 思考强度（可选温度）。留空都继承配置存储值，
 * 与 ``ModelAssignment`` / benchmark 变体 dict 的语义一致。
 */
export default function ModelVariantFields({
  config,
  modelId,
  effort,
  temperature,
  temperatureError = false,
  disabled = false,
  modelAria,
  effortAria,
  temperatureAria,
  onModelIdChange,
  onEffortChange,
  onTemperatureChange,
}: ModelVariantFieldsProps) {
  const { t } = useTranslation();
  const list = modelListFor(config);
  return (
    <Stack
      direction={{ xs: 'column', sm: 'row' }}
      spacing={1}
      useFlexGap
      sx={{ alignItems: 'center', flexWrap: 'wrap' }}
    >
      {list !== null ? (
        <TextField
          select
          size="small"
          disabled={disabled}
          label={t('create.modelFieldLabel')}
          value={modelId ?? ''}
          onChange={(event) => onModelIdChange(event.target.value || null)}
          slotProps={{
            select: { native: true, inputProps: { 'aria-label': modelAria } },
            inputLabel: { shrink: true },
          }}
          sx={{ minWidth: 200, flex: 1 }}
        >
          <option value="">
            {t('create.groupModelDefault', { id: modelDefaultFor(config) })}
          </option>
          {list.map((id) => (
            <option key={id} value={id}>{id}</option>
          ))}
        </TextField>
      ) : (
        <TextField
          size="small"
          disabled={disabled}
          label={t('create.modelFieldLabel')}
          placeholder={config?.model_id ?? ''}
          value={modelId ?? ''}
          onChange={(event) => onModelIdChange(event.target.value.trim() || null)}
          slotProps={{ htmlInput: { 'aria-label': modelAria } }}
          sx={{ minWidth: 200, flex: 1 }}
        />
      )}
      <Select
        size="small"
        displayEmpty
        disabled={disabled}
        value={effort ?? ''}
        inputProps={{ 'aria-label': effortAria }}
        onChange={(event) => onEffortChange(event.target.value || null)}
        sx={{ minWidth: 200 }}
      >
        {effortMenuItems(t)}
      </Select>
      {onTemperatureChange !== undefined && (
        <TextField
          size="small"
          disabled={disabled}
          error={temperatureError}
          label={t('benchmark.variantTemperature')}
          value={temperature ?? ''}
          onChange={(event) => onTemperatureChange(event.target.value)}
          slotProps={{
            htmlInput: {
              inputMode: 'decimal',
              'aria-label': temperatureAria,
            },
          }}
          sx={{ minWidth: 140 }}
        />
      )}
    </Stack>
  );
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

export interface AssignmentQuantityControlsProps {
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

export function AssignmentQuantityControls({
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
}: AssignmentQuantityControlsProps) {
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
