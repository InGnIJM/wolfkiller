import { useEffect, useState } from 'react';
import { Box, Chip, FormControlLabel, IconButton, Stack, Typography } from '@mui/material';
import Checkbox from '@mui/material/Checkbox';
import AddIcon from '@mui/icons-material/Add';
import RemoveIcon from '@mui/icons-material/Remove';

import { fetchConstraints, fetchPresets, fetchRoleCatalog } from '../../api/client';
import type { FieldConstraints, GamePreset, RoleCatalogItem } from '../../store/types';

const CUSTOM_PRESET_ID = 'custom';

interface Props {
  roleCounts: Record<string, number>;
  onRoleCountsChange: (counts: Record<string, number>) => void;
  onConstraintsChange: (constraints: FieldConstraints) => void;
  revealOnDeath: boolean;
  onRevealOnDeathChange: (value: boolean) => void;
}

export default function RoleStep({
  roleCounts, onRoleCountsChange, onConstraintsChange,
  revealOnDeath, onRevealOnDeathChange,
}: Props) {
  const [presets, setPresets] = useState<GamePreset[]>([]);
  const [roles, setRoles] = useState<RoleCatalogItem[]>([]);
  const [constraints, setConstraints] = useState<FieldConstraints | null>(null);
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void Promise.all([fetchPresets(), fetchRoleCatalog(), fetchConstraints()])
      .then(([presetList, roleList, constraintValues]) => {
        if (!active) return;
        setPresets(presetList);
        setRoles(roleList);
        setConstraints(constraintValues);
        onConstraintsChange(constraintValues);
        const nine = presetList.find((p) => p.id === 'nine-player-standard') ?? presetList[0];
        if (nine && Object.keys(roleCounts).length === 0) {
          setSelectedPresetId(nine.id);
          onRoleCountsChange({ ...nine.role_counts });
        }
      })
      .catch((error) => {
        if (active) setLoadError(error instanceof Error ? error.message : String(error));
      });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const total = Object.values(roleCounts).reduce((sum, n) => sum + n, 0);
  const goodCount = roles
    .filter((role) => role.camp === 'good')
    .reduce((sum, role) => sum + (roleCounts[role.role_id] ?? 0), 0);

  const selectPreset = (preset: GamePreset) => {
    setSelectedPresetId(preset.id);
    onRoleCountsChange({ ...preset.role_counts });
  };

  const canIncrement = (role: RoleCatalogItem) => {
    const current = roleCounts[role.role_id] ?? 0;
    if (role.max_count !== null && current >= role.max_count) return false;
    if (!constraints) return true;
    return total < constraints.max_players;
  };

  const canDecrement = (role: RoleCatalogItem) => {
    const current = roleCounts[role.role_id] ?? 0;
    if (current <= 0 || current <= role.min_count) return false;
    if (!constraints) return true;
    if (total - 1 < constraints.min_players) return false;
    if (role.role_id === 'wolf-killer-werewolf' && current - 1 < constraints.min_werewolves) return false;
    if (role.camp === 'good' && goodCount - 1 < constraints.min_good) return false;
    return true;
  };

  const adjust = (role: RoleCatalogItem, delta: number) => {
    const next = {
      ...roleCounts,
      [role.role_id]: Math.max(0, (roleCounts[role.role_id] ?? 0) + delta),
    };
    setSelectedPresetId(CUSTOM_PRESET_ID);
    onRoleCountsChange(next);
  };

  return (
    <Box>
      {loadError && <Typography variant="body2" color="error">{loadError}</Typography>}

      <Typography variant="subtitle2" sx={{ mb: 1 }}>选择标准场或自定义</Typography>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mb: 3 }}>
        {presets.map((preset) => (
          <Box
            key={preset.id}
            onClick={() => selectPreset(preset)}
            sx={{
              flex: 1, cursor: 'pointer', p: 1.5, borderRadius: 2,
              border: '1px solid',
              borderColor: selectedPresetId === preset.id ? 'primary.main' : 'divider',
              bgcolor: 'background.paper',
            }}
          >
            <Typography variant="subtitle2">{preset.name}</Typography>
            <Typography variant="body2" color="text.secondary">{preset.description}</Typography>
          </Box>
        ))}
      </Stack>

      <Typography variant="subtitle2" sx={{ mb: 1 }}>角色配置（点加减即切换为自定义场）</Typography>
      <Stack spacing={1}>
        {roles.map((role) => {
          const count = roleCounts[role.role_id] ?? 0;
          return (
            <Box
              key={role.role_id}
              sx={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                bgcolor: 'background.paper', border: '1px solid',
                borderColor: 'divider', borderRadius: 2, p: 1.5,
              }}
            >
              <Box>
                <Typography variant="body1">{role.name_zh}</Typography>
                <Typography variant="body2" color="text.secondary">{role.description}</Typography>
              </Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Chip
                  size="small"
                  variant="outlined"
                  color={role.camp === 'werewolf' ? 'error' : 'default'}
                  label={role.camp === 'werewolf' ? '狼人阵营' : role.camp === 'good' ? '好人阵营' : role.camp}
                />
                <IconButton
                  size="small"
                  aria-label={`减少${role.name_zh}`}
                  disabled={!canDecrement(role)}
                  onClick={() => adjust(role, -1)}
                >
                  <RemoveIcon />
                </IconButton>
                <Typography sx={{ width: 24, textAlign: 'center' }}>{count}</Typography>
                <IconButton
                  size="small"
                  aria-label={`增加${role.name_zh}`}
                  disabled={!canIncrement(role)}
                  onClick={() => adjust(role, 1)}
                >
                  <AddIcon />
                </IconButton>
              </Box>
            </Box>
          );
        })}
      </Stack>

      <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
        共 {total} 人
      </Typography>

      <Box
        sx={{
          mt: 2, p: 1.5, borderRadius: 2, bgcolor: 'background.paper',
          border: '1px solid', borderColor: 'divider',
        }}
      >
        <FormControlLabel
          control={(
            <Checkbox
              checked={revealOnDeath}
              onChange={(event) => onRevealOnDeathChange(event.target.checked)}
            />
          )}
          label="明牌局：玩家出局时立即公开身份"
          slotProps={{ typography: { variant: 'body2' } }}
        />
        <Typography variant="body2" color="text.secondary">
          开启后，夜晚死亡、放逐、猎人开枪带走的玩家都会当场亮明身份，所有 Agent 与观众都能看到。
        </Typography>
      </Box>
    </Box>
  );
}
