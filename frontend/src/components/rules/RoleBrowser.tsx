import { useTranslation } from 'react-i18next';
import { useEffect, useMemo, useState } from 'react';
import {
  Box, Collapse, IconButton, InputAdornment, Paper, Stack, TextField, Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import SearchIcon from '@mui/icons-material/Search';

import { WINNING_CAMP_LABELS } from '../../labels';
import type { RoleCatalogItem, WinningCamp } from '../../store/types';
import { BLOOD_MOON, CANVAS, HAIRLINE, INK, SHADOW } from '../../theme/tokens';
import RoleIcon from '../shared/RoleIcon';
import RolePortrait from '../shared/RolePortrait';
import { roleMetaFor } from '../shared/roleMeta';

const SEARCH_DEBOUNCE_MS = 200;

interface Props {
  /** 已按阵营分好组的角色，顺序由调用方决定。 */
  groups: readonly { camp: string; roles: RoleCatalogItem[] }[];
}

function campLabel(camp: string): string {
  return camp in WINNING_CAMP_LABELS
    ? WINNING_CAMP_LABELS[camp as WinningCamp]
    : camp;
}

function RuleList({ items }: { items: readonly string[] }) {
  return (
    <Box component="ul" sx={{ m: 0, pl: 2.5, display: 'grid', gap: 0.75 }}>
      {items.map((item) => (
        <Typography key={item} component="li" variant="body2" sx={{ lineHeight: 1.6 }}>
          {item}
        </Typography>
      ))}
    </Box>
  );
}

/**
 * 角色索引 + 详情。角色到 40 个之后一列长卡片读不动，改成左索引右详情。
 * 交互语言与创建向导的左栏一致：搜索防抖、按阵营折叠、条目整行可点。
 */
export default function RoleBrowser({ groups }: Props) {
  const { t } = useTranslation();
  const [rawQuery, setRawQuery] = useState('');
  const [query, setQuery] = useState('');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(rawQuery), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [rawQuery]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return groups;
    return groups
      .map((group) => ({
        camp: group.camp,
        roles: group.roles.filter((role) => (
          role.name_zh.toLowerCase().includes(needle)
          || role.description.toLowerCase().includes(needle)
          || (role.rules ?? []).some((rule) => rule.toLowerCase().includes(needle))
        )),
      }))
      .filter((group) => group.roles.length > 0);
  }, [groups, query]);

  const flat = useMemo(() => filtered.flatMap((group) => group.roles), [filtered]);
  // 选中的角色被搜索过滤掉时，回落到当前列表的第一个，避免右侧停在一个看不见的角色上。
  const selected = flat.find((role) => role.role_id === selectedId) ?? flat[0] ?? null;
  const searching = query.trim().length > 0;

  if (groups.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary">
        {t('rules.rolesEmpty')}
      </Typography>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, gap: 2, mt: 1 }}>
      <Box
        sx={{
          width: { xs: '100%', md: 260 }, flexShrink: 0, display: 'flex',
          flexDirection: 'column', minHeight: 0,
          border: `1px solid ${HAIRLINE.soft}`, borderRadius: 1, bgcolor: CANVAS.surface,
        }}
      >
        <Box sx={{ p: 1.25 }}>
          <TextField
            value={rawQuery}
            onChange={(event) => setRawQuery(event.target.value)}
            placeholder={t('rules.searchRoles')}
            size="small"
            fullWidth
            slotProps={{
              htmlInput: { 'aria-label': t('rules.searchRolesAria') },
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
                      aria-label={t('rules.clearRoleSearch')}
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

        <Box sx={{ flex: 1, minHeight: 0, maxHeight: { md: 520 }, overflowY: 'auto', pb: 1 }}>
          {filtered.length === 0 && (
            <Box sx={{ px: 1.75, py: 2 }}>
              <Typography variant="body2" sx={{ color: INK.dim }}>{t('rules.noRoleMatch')}</Typography>
              <Typography variant="caption" sx={{ color: INK.faint, display: 'block', mt: 0.5 }}>
                {t('rules.tryRoleSearch')}
              </Typography>
            </Box>
          )}
          {filtered.map((group) => {
            const open = searching || !(collapsed[group.camp] ?? false);
            return (
              <Box key={group.camp}>
                <Box
                  component="button"
                  type="button"
                  aria-expanded={open}
                  onClick={() => setCollapsed((prev) => ({ ...prev, [group.camp]: open }))}
                  sx={{
                    display: 'flex', alignItems: 'center', gap: 0.75, width: '100%',
                    minHeight: 40, px: 1.5, border: 0, cursor: 'pointer',
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
                    {campLabel(group.camp)}
                  </Typography>
                  <Typography variant="caption" sx={{
                    color: INK.faint, fontVariantNumeric: 'tabular-nums',
                  }}>
                    {group.roles.length}
                  </Typography>
                </Box>
                <Collapse in={open} timeout="auto" unmountOnExit>
                  <Stack spacing={0.25} sx={{ px: 0.75, pb: 0.5 }}>
                    {group.roles.map((role) => {
                      const on = role.role_id === selected?.role_id;
                      return (
                        <Box
                          key={role.role_id}
                          component="button"
                          type="button"
                          aria-pressed={on}
                          aria-label={role.name_zh}
                          onClick={() => setSelectedId(role.role_id)}
                          sx={{
                            display: 'flex', alignItems: 'center', gap: 1, width: '100%',
                            minHeight: 44, px: 0.75, py: 0.5, cursor: 'pointer',
                            border: 0, borderLeft: '2px solid',
                            borderColor: on ? BLOOD_MOON.crimsonDeep : 'transparent',
                            borderRadius: 1, color: 'inherit', textAlign: 'left',
                            bgcolor: on ? CANVAS.surfaceRaised : 'transparent',
                            transition: (theme) => theme.transitions.create('background-color', {
                              duration: theme.transitions.duration.shorter,
                            }),
                            '&:hover': { bgcolor: CANVAS.surfaceRaised },
                          }}
                        >
                          <RolePortrait roleId={role.role_id} width={28} height={36} showLabel={false} />
                          <Typography
                            variant="body2"
                            sx={{ color: on ? INK.primary : INK.dim, fontWeight: on ? 700 : 400 }}
                          >
                            {role.name_zh}
                          </Typography>
                        </Box>
                      );
                    })}
                  </Stack>
                </Collapse>
              </Box>
            );
          })}
        </Box>
      </Box>

      <Box sx={{ flex: 1, minWidth: 0 }}>
        {selected === null ? (
          <Typography variant="body2" color="text.secondary">
            {t('rules.pickRoleHint')}
          </Typography>
        ) : (
          <Paper
            variant="outlined"
            data-testid={`role-card-${selected.role_id}`}
            sx={{ p: 2, borderColor: HAIRLINE.soft, overflow: 'hidden' }}
          >
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={2}
              sx={{ alignItems: { xs: 'stretch', sm: 'flex-start' } }}
            >
              <Box
                sx={{
                  width: { xs: '100%', sm: 168 },
                  maxWidth: { xs: 180, sm: 'none' },
                  mx: { xs: 'auto', sm: 0 },
                  flexShrink: 0,
                  borderRadius: 1,
                  overflow: 'hidden',
                  border: `1px solid ${HAIRLINE.strong}`,
                  boxShadow: `0 10px 24px ${SHADOW.soft}`,
                }}
              >
                <RolePortrait roleId={selected.role_id} width="100%" showLabel={false} />
              </Box>
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                  <RoleIcon role={selected.role_id} size={22} />
                  <Typography
                    variant="h6"
                    sx={{ color: roleMetaFor(selected.role_id)?.color ?? 'text.primary' }}
                  >
                    {selected.name_zh}
                  </Typography>
                </Stack>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                  {selected.description}
                </Typography>
                <Box sx={{ mt: 1.5 }}>
                  {(selected.rules ?? []).length > 0 ? (
                    <RuleList items={selected.rules ?? []} />
                  ) : (
                    <Typography variant="body2" color="text.secondary">
                      {t('rules.noDetailRules')}
                    </Typography>
                  )}
                </Box>
              </Box>
            </Stack>
          </Paper>
        )}
      </Box>
    </Box>
  );
}
