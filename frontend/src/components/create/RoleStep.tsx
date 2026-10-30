import { useTranslation } from 'react-i18next';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Box, Button, Checkbox, FormControlLabel, Skeleton, Stack, Typography,
} from '@mui/material';
import AddIcon from '@mui/icons-material/Add';

import { fetchConstraints, fetchPresets, fetchRoleCatalog } from '../../api/client';
import type {
  FieldConstraints, GamePreset, RoleCatalogItem, VictoryPolicy, WitchSelfSavePolicy,
} from '../../store/types';
import { BLOOD_MOON, HAIRLINE, INK } from '../../theme/tokens';
import { rankOf } from '../shared/roleMeta';
import AddRoleDialog from './AddRoleDialog';
import PresetList from './PresetList';
import RoleRow from './RoleRow';

const DEFAULT_PRESET_ID = 'nine-player-standard';

interface Props {
  roleCounts: Record<string, number>;
  onRoleCountsChange: (counts: Record<string, number>) => void;
  onConstraintsChange: (constraints: FieldConstraints) => void;
  onRolesChange: (roles: RoleCatalogItem[]) => void;
  revealOnDeath: boolean;
  onRevealOnDeathChange: (value: boolean) => void;
  enableSheriff: boolean;
  onEnableSheriffChange: (value: boolean) => void;
  witchSelfSave: WitchSelfSavePolicy;
  onWitchSelfSaveChange: (value: WitchSelfSavePolicy) => void;
  victory: VictoryPolicy;
  onVictoryChange: (value: VictoryPolicy) => void;
}

export default function RoleStep({
  roleCounts, onRoleCountsChange, onConstraintsChange, onRolesChange,
  revealOnDeath, onRevealOnDeathChange,
  enableSheriff, onEnableSheriffChange,
  witchSelfSave, onWitchSelfSaveChange, victory, onVictoryChange,
}: Props) {
  const { t } = useTranslation();
  const [presets, setPresets] = useState<GamePreset[]>([]);
  const [roles, setRoles] = useState<RoleCatalogItem[]>([]);
  const [constraints, setConstraints] = useState<FieldConstraints | null>(null);
  /** 当前基于哪个板子；点板子或重置时更新。 */
  const [basePresetId, setBasePresetId] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  // 窄屏的板子选择折叠状态（桌面端由断点强制展开，见下方 sx）。
  const [pickerOpen, setPickerOpen] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const applyPreset = useCallback((preset: GamePreset) => {
    setBasePresetId(preset.id);
    onRoleCountsChange({ ...preset.role_counts });
    onEnableSheriffChange(Boolean(preset.enable_sheriff));
    onWitchSelfSaveChange(preset.witch_self_save ?? 'first_night_only');
    onVictoryChange(preset.victory ?? 'edge');
  }, [onEnableSheriffChange, onRoleCountsChange, onVictoryChange, onWitchSelfSaveChange]);

  useEffect(() => {
    let active = true;
    void Promise.all([fetchPresets(), fetchRoleCatalog(), fetchConstraints()])
      .then(([presetList, roleList, constraintValues]) => {
        if (!active) return;
        setPresets(presetList);
        setRoles(roleList);
        setConstraints(constraintValues);
        onConstraintsChange(constraintValues);
        onRolesChange(roleList);
        setLoading(false);
        const fallback = presetList.find((p) => p.id === DEFAULT_PRESET_ID) ?? presetList[0];
        if (fallback && Object.keys(roleCounts).length === 0) {
          applyPreset(fallback);
        } else {
          setBasePresetId((current) => current ?? presetList[0]?.id ?? null);
        }
      })
      .catch((error) => {
        if (!active) return;
        setLoading(false);
        setLoadError(error instanceof Error ? error.message : String(error));
      });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const total = Object.values(roleCounts).reduce((sum, n) => sum + n, 0);
  const campCount = (camp: string) => roles
    .filter((role) => role.camp === camp)
    .reduce((sum, role) => sum + (roleCounts[role.role_id] ?? 0), 0);
  // 狼队人数按阵营汇总：白狼王等狼阵营变体与普通狼人一并计入
  const wolfCount = campCount('werewolf');
  const goodCount = campCount('good');
  const thirdCount = campCount('third_party');

  const basePreset = useMemo(
    () => presets.find((preset) => preset.id === basePresetId) ?? null,
    [presets, basePresetId],
  );
  /**
   * 是否已偏离原板子 —— 直接和板子的 role_counts 比，不用一个 isCustom 布尔量。
   *
   * 用布尔量会在「切到第二步再切回来」时失真：组件重新挂载、布尔量归位，而
   * roleCounts 由父级保留着，于是标题显示板子名、阵容却已经不是那个板子。
   */
  const isCustom = useMemo(() => {
    if (!basePreset) return false;
    const base = basePreset.role_counts;
    const keys = new Set([...Object.keys(base), ...Object.keys(roleCounts)]);
    for (const key of keys) {
      if ((base[key] ?? 0) !== (roleCounts[key] ?? 0)) return true;
    }
    return false;
  }, [basePreset, roleCounts]);
  /** 原板子里就有的角色；不在这个集合里的已选角色显示「新增」角标。 */
  const baseRoleIds = useMemo(
    () => new Set(Object.keys(basePreset?.role_counts ?? {})),
    [basePreset],
  );
  /** 右栏只显示数量大于 0 的角色。 */
  const activeRoles = useMemo(
    () => roles
      .filter((role) => (roleCounts[role.role_id] ?? 0) > 0)
      .sort((a, b) => rankOf(a.role_id) - rankOf(b.role_id)),
    [roles, roleCounts],
  );
  /** 「+ 添加角色」只列这个板子里还没有的角色。 */
  const candidates = useMemo(
    () => roles
      .filter((role) => (roleCounts[role.role_id] ?? 0) === 0)
      .sort((a, b) => rankOf(a.role_id) - rankOf(b.role_id)),
    [roles, roleCounts],
  );

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
    if (role.camp === 'werewolf' && wolfCount - 1 < constraints.min_werewolves) return false;
    if (role.camp === 'good' && goodCount - 1 < constraints.min_good) return false;
    return true;
  };

  const adjust = (role: RoleCatalogItem, delta: number) => {
    onRoleCountsChange({
      ...roleCounts,
      [role.role_id]: Math.max(0, (roleCounts[role.role_id] ?? 0) + delta),
    });
  };

  const addRoles = (roleIds: string[]) => {
    setAddOpen(false);
    const next = { ...roleCounts };
    for (const roleId of roleIds) next[roleId] = (next[roleId] ?? 0) + 1;
    onRoleCountsChange(next);
  };

  const overLimit = constraints !== null && total > constraints.max_players;
  const underLimit = constraints !== null && total < constraints.min_players;

  return (
    <Box sx={{
      display: 'flex',
      flexDirection: { xs: 'column', md: 'row' },
      alignItems: 'stretch',
      minHeight: { xs: 0, md: 520 },
    }}>
      {/* 窄屏把板子列表折到上面并默认收起：290px 固定左栏在 375px 屏上会把
          右栏挤到只剩几十像素，横向溢出 65px（2026-10-05 实测）。桌面端这个
          按钮不显示，列表由断点强制展开，所以状态不会影响大屏。 */}
      <Button
        size="small"
        aria-expanded={pickerOpen}
        onClick={() => setPickerOpen((open) => !open)}
        sx={{ display: { xs: 'inline-flex', md: 'none' }, alignSelf: 'flex-start', m: 1.5, mb: 0 }}
      >
        {t('create.selectBoard')}
      </Button>
      <Box
        sx={{
          display: pickerOpen ? 'flex' : { xs: 'none', md: 'flex' },
          flexDirection: 'column',
          minWidth: 0,
          minHeight: 0,
        }}
      >
        <PresetList
          presets={presets}
          roles={roles}
          selectedPresetId={basePresetId}
          onSelect={applyPreset}
        />
      </Box>

      <Box sx={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <Box sx={{ px: 2.25, pt: 1.75, pb: 1.5, borderBottom: `1px solid ${HAIRLINE.soft}` }}>
          {loadError && (
            <Typography variant="body2" color="error">{loadError}</Typography>
          )}
          <Stack
            direction="row"
            spacing={1.5}
            sx={{ alignItems: 'flex-start', justifyContent: 'space-between' }}
          >
            <Box sx={{ minWidth: 0 }}>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
                <Typography variant="h6" sx={{ lineHeight: 1.3 }}>
                  {isCustom ? t('create.customRoster') : (basePreset?.name ?? t('create.selectBoard'))}
                </Typography>
                {isCustom && basePreset && (
                  <Box
                    component="span"
                    sx={{
                      fontSize: 10, px: 0.75, py: 0.25, borderRadius: 0.5,
                      border: `1px dashed ${HAIRLINE.strong}`, color: INK.dim,
                    }}
                  >
                    {t('create.basedOn', { name: basePreset.name })}
                  </Box>
                )}
                {isCustom && basePreset && (
                  <Button
                    size="small"
                    color="inherit"
                    sx={{ color: BLOOD_MOON.gold, textTransform: 'none', minWidth: 0 }}
                    onClick={() => applyPreset(basePreset)}
                  >
                    {t('create.resetBoard')}
                  </Button>
                )}
              </Stack>
              {basePreset && (
                <Typography variant="caption" sx={{ display: 'block', color: INK.dim, mt: 0.5 }}>
                  {basePreset.description}
                </Typography>
              )}
            </Box>
            <Button
              variant="outlined"
              size="small"
              startIcon={<AddIcon />}
              onClick={() => setAddOpen(true)}
              sx={{ flexShrink: 0, borderColor: HAIRLINE.strong, color: BLOOD_MOON.gold }}
            >
              {t('create.addRole')}
            </Button>
          </Stack>

          <Stack direction="row" spacing={1} sx={{ mt: 1.25, flexWrap: 'wrap', gap: 0.75 }}>
            <Stat
              label={t('create.statTotal')} value={total} suffix={t('create.statTotalSuffix')}
              tone={overLimit ? 'warn' : 'plain'}
              testId="roster-total"
            />
            <Stat label={t('create.statWolf')} value={wolfCount} tone="wolf" />
            <Stat label={t('create.statGood')} value={goodCount} />
            {thirdCount > 0 && <Stat label={t('create.statThird')} value={thirdCount} />}
            <Stat label={t('create.statSheriff')} text={t(enableSheriff ? 'create.statOn' : 'create.statOff')} />
            <Stat label={t('create.statWitchSave')} text={t(witchSelfSave === 'never' ? 'create.witchNever' : 'create.witchFirstNight')} />
            <Stat label={t('create.statVictory')} text={t(victory === 'city' ? 'create.victoryCity' : 'create.victoryEdge')} />
            {overLimit && (
              <Typography variant="caption" sx={{ color: BLOOD_MOON.crimson, alignSelf: 'center' }}>
                {t('create.overBy', { count: total - (constraints?.max_players ?? total) })}
              </Typography>
            )}
            {underLimit && constraints && (
              <Typography variant="caption" sx={{ color: INK.faint, alignSelf: 'center' }}>
                {t('create.shortBy', { count: constraints.min_players - total })}
              </Typography>
            )}
          </Stack>
        </Box>

        <Box sx={{ flex: 1, minHeight: 0, overflowY: 'auto', p: 2.25, pt: 1.75 }}>
          {loading && (
            <Stack spacing={1}>
              {[0, 1, 2, 3, 4, 5].map((key) => (
                <Skeleton key={key} variant="rounded" height={62} />
              ))}
            </Stack>
          )}
          {!loading && activeRoles.length === 0 && (
            <Typography variant="body2" sx={{ color: INK.dim }}>
              {t('create.emptyRoster')}
            </Typography>
          )}
          <Stack spacing={1}>
            {activeRoles.map((role) => (
              <RoleRow
                key={role.role_id}
                role={role}
                count={roleCounts[role.role_id] ?? 0}
                added={isCustom && !baseRoleIds.has(role.role_id)}
                canIncrement={canIncrement(role)}
                canDecrement={canDecrement(role)}
                onChange={(delta) => adjust(role, delta)}
              />
            ))}
          </Stack>

          <Box sx={{ mt: 2.5, pt: 2, borderTop: `1px solid ${HAIRLINE.soft}` }}>
            <FormControlLabel
              control={(
                <Checkbox
                  checked={revealOnDeath}
                  onChange={(event) => onRevealOnDeathChange(event.target.checked)}
                />
              )}
              label={t('create.revealLabel')}
              slotProps={{ typography: { variant: 'body2' } }}
            />
            <Typography variant="caption" sx={{ display: 'block', color: INK.faint, ml: 4 }}>
              {t('create.revealHint')}
            </Typography>
            <FormControlLabel
              control={(
                <Checkbox
                  checked={enableSheriff}
                  onChange={(event) => onEnableSheriffChange(event.target.checked)}
                />
              )}
              label={t('create.sheriffLabel')}
              slotProps={{ typography: { variant: 'body2' } }}
            />
            <Typography variant="caption" sx={{ display: 'block', color: INK.faint, ml: 4 }}>
              {t('create.sheriffHint')}
            </Typography>
            <Typography variant="caption" sx={{ display: 'block', color: INK.faint, mt: 1 }}>
              {t('create.victoryLabel', {
                desc: t(victory === 'city' ? 'create.victoryCityDesc' : 'create.victoryEdgeDesc'),
              })}
            </Typography>
            <Typography variant="caption" sx={{ display: 'block', color: INK.faint, mt: 1 }}>
              {t('create.witchLabel', {
                desc: t(witchSelfSave === 'never' ? 'create.witchNeverDesc' : 'create.witchFirstNightDesc'),
              })}
            </Typography>
          </Box>
        </Box>
      </Box>

      <AddRoleDialog
        open={addOpen}
        candidates={candidates}
        onClose={() => setAddOpen(false)}
        onConfirm={addRoles}
      />
    </Box>
  );
}

function Stat({
  label, value, text, suffix, tone = 'plain', testId,
}: {
  label: string;
  value?: number;
  text?: string;
  suffix?: string;
  tone?: 'plain' | 'wolf' | 'warn';
  testId?: string;
}) {
  const color = tone === 'wolf' || tone === 'warn' ? BLOOD_MOON.crimson : INK.dim;
  return (
    <Box
      data-testid={testId}
      sx={{
        border: `1px solid ${tone === 'wolf' ? 'rgba(168, 55, 74, 0.4)' : HAIRLINE.soft}`,
        borderRadius: 0.5, px: 1, py: 0.25, fontSize: 11, color,
      }}
    >
      {label}{' '}
      <Box
        component="span"
        sx={{
          color: tone === 'plain' ? INK.primary : color,
          fontWeight: 700,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {text ?? value}
      </Box>
      {suffix && value !== undefined ? ` ${suffix}` : ''}
    </Box>
  );
}
