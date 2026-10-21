import { useEffect, useMemo, useState } from 'react';
import {
  Box, Collapse, IconButton, InputAdornment, Stack, TextField, Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import SearchIcon from '@mui/icons-material/Search';

import type { GamePreset, RoleCatalogItem } from '../../store/types';
import { BLOOD_MOON, CANVAS, HAIRLINE, INK } from '../../theme/tokens';

/** 搜索防抖：40 个角色 × 多个板子的匹配不必每次按键都重算。 */
const SEARCH_DEBOUNCE_MS = 200;

interface Props {
  presets: readonly GamePreset[];
  /** 角色目录，用于把板子阵容翻译成中文名（搜索要能搜到「摄梦」）与阵营配色。 */
  roles: readonly RoleCatalogItem[];
  /** 当前选中的板子 id；`custom` 表示已基于某个板子改过。 */
  selectedPresetId: string | null;
  onSelect: (preset: GamePreset) => void;
}

/** 板子是否按网易官方赛制口径（开警长 + 女巫全程不可自救）。 */
function isOfficial(preset: GamePreset): boolean {
  return Boolean(preset.enable_sheriff) && preset.witch_self_save === 'never';
}

export default function PresetList({
  presets, roles, selectedPresetId, onSelect,
}: Props) {
  const [rawQuery, setRawQuery] = useState('');
  const [query, setQuery] = useState('');
  const [collapsed, setCollapsed] = useState<Record<number, boolean>>({});

  useEffect(() => {
    const timer = setTimeout(() => setQuery(rawQuery), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [rawQuery]);

  const roleById = useMemo(
    () => new Map(roles.map((role) => [role.role_id, role])),
    [roles],
  );

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return presets;
    return presets.filter((preset) => {
      if (preset.name.toLowerCase().includes(needle)) return true;
      if (preset.description.toLowerCase().includes(needle)) return true;
      // 阵容里的角色中文名也参与匹配，这样搜「摄梦」能找到狼王摄梦人板。
      return Object.keys(preset.role_counts).some(
        (roleId) => roleById.get(roleId)?.name_zh.toLowerCase().includes(needle),
      );
    });
  }, [presets, query, roleById]);

  const groups = useMemo(() => {
    const bySize = new Map<number, GamePreset[]>();
    for (const preset of matches) {
      const size = Object.values(preset.role_counts).reduce((sum, n) => sum + n, 0);
      bySize.set(size, [...(bySize.get(size) ?? []), preset]);
    }
    return [...bySize.entries()].sort((a, b) => b[0] - a[0]);
  }, [matches]);

  const searching = query.trim().length > 0;
  // 默认全展开：9 人 / 10 人组各只有一个板子，折叠掉会让默认选中的九人场都看不见。
  // 折叠是留给板子变多之后的主动动作，搜索时强制全展开。
  const isOpen = (size: number) => searching || !(collapsed[size] ?? false);

  return (
    <Box
      sx={{
        width: { xs: '100%', md: 290 },
        flexShrink: 0, display: 'flex', flexDirection: 'column',
        minHeight: 0,
        borderRight: { xs: 'none', md: `1px solid ${HAIRLINE.soft}` },
        borderBottom: { xs: `1px solid ${HAIRLINE.soft}`, md: 'none' },
        bgcolor: CANVAS.surface,
      }}
    >
      <Box sx={{ p: 1.5, pb: 1 }}>
        <TextField
          value={rawQuery}
          onChange={(event) => setRawQuery(event.target.value)}
          placeholder="搜索板子 / 角色名…"
          size="small"
          fullWidth
          slotProps={{
            htmlInput: { 'aria-label': '搜索板子' },
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon sx={{ fontSize: 18, color: INK.faint }} />
                </InputAdornment>
              ),
              endAdornment: rawQuery ? (
                <InputAdornment position="end">
                  <IconButton
                    size="small"
                    aria-label="清空搜索"
                    onClick={() => setRawQuery('')}
                  >
                    <CloseIcon sx={{ fontSize: 16 }} />
                  </IconButton>
                </InputAdornment>
              ) : null,
            },
          }}
        />
      </Box>

      <Box
        data-testid="preset-list-scroll"
        data-max-height="520"
        sx={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          maxHeight: { xs: 280, md: 520 },
          pb: 1,
        }}
      >
        {groups.length === 0 && (
          <Box sx={{ px: 2, py: 3 }}>
            <Typography variant="body2" sx={{ color: INK.dim }}>
              没有匹配的板子
            </Typography>
            <Typography variant="caption" sx={{ color: INK.faint, display: 'block', mt: 0.5 }}>
              试试「狼王」或「摄梦」，或清空搜索
            </Typography>
          </Box>
        )}

        {groups.map(([size, items]) => {
          const open = isOpen(size);
          return (
            <Box key={size}>
              <Box
                component="button"
                type="button"
                aria-expanded={open}
                onClick={() => setCollapsed((prev) => ({ ...prev, [size]: open }))}
                sx={{
                  display: 'flex', alignItems: 'center', gap: 0.75, width: '100%',
                  minHeight: 40, px: 1.5, py: 1, border: 0, cursor: 'pointer',
                  bgcolor: 'transparent', color: 'inherit', textAlign: 'left',
                  '&:hover': { bgcolor: CANVAS.surfaceRaised },
                }}
              >
                <ExpandMoreIcon
                  sx={{
                    fontSize: 16, color: BLOOD_MOON.gold,
                    transform: open ? 'none' : 'rotate(-90deg)',
                    transition: (theme) => theme.transitions.create('transform', {
                      duration: theme.transitions.duration.shorter,
                    }),
                  }}
                />
                <Typography variant="caption" sx={{
                  color: BLOOD_MOON.gold, fontWeight: 600, letterSpacing: '0.08em',
                }}>
                  {size} 人局
                </Typography>
                <Typography variant="caption" sx={{
                  color: INK.faint, fontVariantNumeric: 'tabular-nums',
                }}>
                  {items.length}
                </Typography>
              </Box>

              <Collapse in={open} timeout="auto" unmountOnExit>
                <Stack spacing={0.5} sx={{ px: 1, pb: 1 }}>
                  {items.map((preset) => (
                    <PresetItem
                      key={preset.id}
                      preset={preset}
                      roleById={roleById}
                      selected={preset.id === selectedPresetId}
                      onSelect={onSelect}
                    />
                  ))}
                </Stack>
              </Collapse>
            </Box>
          );
        })}
      </Box>
    </Box>
  );
}

function PresetItem({
  preset, roleById, selected, onSelect,
}: {
  preset: GamePreset;
  roleById: Map<string, RoleCatalogItem>;
  selected: boolean;
  onSelect: (preset: GamePreset) => void;
}) {
  return (
    <Box
      component="button"
      type="button"
      onClick={() => onSelect(preset)}
      aria-pressed={selected}
      aria-label={preset.name}
      sx={{
        display: 'block', width: '100%', textAlign: 'left', cursor: 'pointer',
        border: 0, borderLeft: '2px solid', borderRadius: 1,
        borderColor: selected ? BLOOD_MOON.crimsonDeep : 'transparent',
        bgcolor: selected ? CANVAS.surfaceRaised : 'transparent',
        color: 'inherit', px: 1.25, py: 0.75, minHeight: 44,
        transition: (theme) => theme.transitions.create('background-color', {
          duration: theme.transitions.duration.shorter,
        }),
        '&:hover': { bgcolor: CANVAS.surfaceRaised },
      }}
    >
      <Stack direction="row" spacing={0.75} sx={{ alignItems: 'center', minWidth: 0 }}>
        <Typography
          variant="body2"
          title={preset.name}
          sx={{
            fontWeight: selected ? 700 : 400, color: selected ? INK.primary : INK.dim,
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}
        >
          {preset.name}
        </Typography>
        {isOfficial(preset) && (
          <Box
            component="span"
            sx={{
              flexShrink: 0, fontSize: 9, lineHeight: '14px', px: 0.5,
              border: `1px solid ${BLOOD_MOON.gold}`, color: BLOOD_MOON.gold,
              borderRadius: 0.5,
            }}
          >
            官方
          </Box>
        )}
      </Stack>
      <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.25, mt: 0.25 }}>
        {Object.entries(preset.role_counts).map(([roleId, count]) => {
          const role = roleById.get(roleId);
          const wolf = role?.camp === 'werewolf';
          return (
            <Box
              key={roleId}
              component="span"
              sx={{
                fontSize: 9, lineHeight: '14px', px: 0.4, borderRadius: 0.5,
                border: `1px solid ${wolf ? BLOOD_MOON.crimsonDeep : HAIRLINE.soft}`,
                color: wolf ? BLOOD_MOON.crimson : INK.dim,
              }}
            >
              {role?.name_zh ?? roleId}{count > 1 ? `×${count}` : ''}
            </Box>
          );
        })}
      </Stack>
    </Box>
  );
}
