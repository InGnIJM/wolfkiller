import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Box, Button, Chip, IconButton, Stack, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import RemoveIcon from '@mui/icons-material/Remove';

import ModelConfigDialog from '../models/ModelConfigDialog';
import { useModelConfigStore } from '../../store/modelConfigStore';
import type { ModelAssignment, ModelConfigInput } from '../../store/types';

const ENV_DEFAULT_NAME = '环境默认 (.env)';

interface Props {
  totalPlayers: number;
  assignments: ModelAssignment[];
  onAssignmentsChange: (assignments: ModelAssignment[]) => void;
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

export default function ModelStep({
  totalPlayers, assignments, onAssignmentsChange, disabled = false,
}: Props) {
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
        ? '密钥失效，请先更新配置，或将人数减为 0'
        : loadError
          ? '无法确认此配置仍然可用，请改用环境默认模型或稍后重试'
          : null,
    }));
    const orphanRows = assignments
      .filter((entry) => entry.config_id !== null && !configById.has(entry.config_id))
      .map((entry) => ({
        configId: entry.config_id,
        name: `已删除配置 ${entry.config_id}`,
        detail: '此配置已不在模型列表中',
        invalidReason: '配置已删除或不可用，请将人数减为 0',
      }));
    return [
      {
        configId: null,
        name: ENV_DEFAULT_NAME,
        detail: '使用服务端环境变量中的默认模型',
        invalidReason: null,
      },
      ...storedRows,
      ...orphanRows,
    ];
  }, [assignments, configById, configs, loadError]);

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
        为 {totalPlayers} 位玩家分配模型
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        按人数随机落座；同一座位在整局中始终使用同一个模型。
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
          已分配 {assignedTotal} / 总人数 {totalPlayers}
        </Typography>
        <Chip
          size="small"
          color={difference === 0 ? 'success' : 'warning'}
          variant="outlined"
          label={
            difference === 0
              ? '分配完成'
              : difference > 0
                ? `还需分配 ${difference} 人`
                : `已超出 ${Math.abs(difference)} 人`
          }
        />
      </Box>

      {loading && (
        <Typography role="status" variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          正在加载已存模型配置…
        </Typography>
      )}
      {error && (
        <Alert severity="warning" sx={{ mb: 1 }}>
          {error}；仍可将全部玩家分配给环境默认模型。
        </Alert>
      )}

      <Stack spacing={1}>
        {rows.map((row) => {
          const count = countFor(assignments, row.configId);
          const invalid = Boolean(row.invalidReason);
          const decreaseLabel = row.configId === null
            ? '减少环境默认模型人数'
            : `减少 ${row.name} 人数`;
          const increaseLabel = row.configId === null
            ? '增加环境默认模型人数'
            : `增加 ${row.name} 人数`;
          const isPendingFocus = row.configId !== null && row.configId === focusConfigId;
          return (
            <Box
              key={row.configId ?? 'environment-default'}
              ref={isPendingFocus ? focusRowRef : undefined}
              role="group"
              aria-label={`${row.name} 人数分配`}
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
                  {invalid && <Chip size="small" color="warning" label="不可用" />}
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

              <Box
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: { xs: 'space-between', sm: 'flex-end' },
                  gap: 1,
                  minWidth: { sm: 152 },
                }}
              >
                <IconButton
                  aria-label={decreaseLabel}
                  disabled={disabled || count <= 0}
                  onClick={() => updateCount(row.configId, count - 1)}
                  sx={{ width: 48, height: 48 }}
                >
                  <RemoveIcon />
                </IconButton>
                <Typography
                  aria-label={`${row.name} 已分配 ${count} 人`}
                  sx={{
                    width: 40,
                    textAlign: 'center',
                    fontVariantNumeric: 'tabular-nums',
                    fontWeight: 600,
                  }}
                >
                  {count}
                </Typography>
                <IconButton
                  aria-label={increaseLabel}
                  disabled={
                    disabled
                    || invalid
                    || (row.configId !== null && loading)
                    || assignedTotal >= totalPlayers
                  }
                  onClick={() => updateCount(row.configId, count + 1)}
                  sx={{ width: 48, height: 48 }}
                >
                  <AddIcon />
                </IconButton>
              </Box>
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
          当场新建模型配置
        </Button>
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
