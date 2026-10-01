import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Alert, Box, Button, Chip, CircularProgress, Container, Paper, Stack, Typography,
} from '@mui/material';

import { fetchConstraints, fetchPresets, fetchRoleCatalog } from '../../api/client';
import { WINNING_CAMP_LABELS } from '../../labels';
import type { FieldConstraints, GamePreset, RoleCatalogItem } from '../../store/types';
import { BACKDROP, HAIRLINE } from '../../theme/tokens';
import RoleIcon from '../shared/RoleIcon';
import { roleMetaFor } from '../shared/roleMeta';
import { RULES_SUBTITLE, RULE_SECTIONS } from './ruleSections';
import type { RuleSection } from './ruleSections';

/** 阵营阅读顺序：狼人在前，未知阵营垫底（后端新增阵营也不会漏渲染）。 */
const CAMP_ORDER: readonly string[] = ['werewolf', 'good'];

/** 角色卡阅读顺序；不在表里的角色按后端顺序排在后面。 */
const ROLE_ORDER: readonly string[] = [
  'wolf-killer-werewolf',
  'wolf-killer-werewolf-king',
  'wolf-killer-wolf-beauty',
  'wolf-killer-seer',
  'wolf-killer-witch',
  'wolf-killer-hunter',
  'wolf-killer-guard',
  'wolf-killer-idiot',
  'wolf-killer-knight',
  'wolf-killer-old-drunkard',
  'wolf-killer-villager',
];

function rankOf(value: string, order: readonly string[]): number {
  const index = order.indexOf(value);
  return index === -1 ? order.length : index;
}

function campLabel(camp: string): string {
  return camp === 'good' || camp === 'werewolf' ? WINNING_CAMP_LABELS[camp] : camp;
}

function groupByCamp(roles: RoleCatalogItem[]): { camp: string; roles: RoleCatalogItem[] }[] {
  const camps = [...new Set(roles.map((role) => role.camp))]
    .sort((a, b) => rankOf(a, CAMP_ORDER) - rankOf(b, CAMP_ORDER));
  return camps.map((camp) => ({
    camp,
    roles: roles
      .filter((role) => role.camp === camp)
      .sort((a, b) => rankOf(a.role_id, ROLE_ORDER) - rankOf(b.role_id, ROLE_ORDER)),
  }));
}

function RuleList({ items, ordered }: { items: readonly string[]; ordered?: boolean }) {
  return (
    <Box
      component={ordered ? 'ol' : 'ul'}
      sx={{ m: 0, pl: 2.5, display: 'grid', gap: 0.5 }}
    >
      {items.map((item) => (
        <Typography key={item} component="li" variant="body2" color="text.secondary">
          {item}
        </Typography>
      ))}
    </Box>
  );
}

function Section({ title, intro, children }: {
  title: string;
  intro?: string;
  children: ReactNode;
}) {
  return (
    <Box component="section" sx={{ mt: 4 }}>
      <Typography component="h2" variant="h6" sx={{ color: 'secondary.main' }}>{title}</Typography>
      {intro && (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{intro}</Typography>
      )}
      <Box sx={{ mt: 1.5 }}>{children}</Box>
    </Box>
  );
}

export default function RulesPage() {
  const [presets, setPresets] = useState<GamePreset[]>([]);
  const [roles, setRoles] = useState<RoleCatalogItem[]>([]);
  const [constraints, setConstraints] = useState<FieldConstraints | null>(null);
  const [presetId, setPresetId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    void Promise.all([fetchPresets(), fetchRoleCatalog(), fetchConstraints()])
      .then(([presetList, roleList, constraintValues]) => {
        if (!active) return;
        setPresets(presetList);
        setRoles(roleList);
        setConstraints(constraintValues);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      });
    return () => { active = false; };
  }, []);

  const selected = presets.find((preset) => preset.id === presetId) ?? presets[0] ?? null;
  const nameOf = (roleId: string) => roles.find((role) => role.role_id === roleId)?.name_zh ?? roleId;
  const composition = selected
    ? Object.entries(selected.role_counts)
      .filter(([, count]) => count > 0)
      .sort(([a], [b]) => rankOf(a, ROLE_ORDER) - rankOf(b, ROLE_ORDER))
    : [];
  const total = composition.reduce((sum, [, count]) => sum + count, 0);
  const groups = groupByCamp(roles);

  return (
    <Container
      component="main"
      maxWidth="lg"
      sx={{
        position: 'relative', zIndex: 1,
        py: { xs: 2, sm: 4 }, px: { xs: 2, sm: 3 },
        bgcolor: BACKDROP.textPlate, backdropFilter: 'blur(12px)', borderRadius: 2,
      }}
    >
      <Typography component="h1" variant="h4">规则介绍</Typography>
      <Typography color="text.secondary" sx={{ mt: 0.5 }}>{RULES_SUBTITLE}</Typography>

      {error && <Alert severity="error" sx={{ mt: 2 }}>{error}</Alert>}
      {loading && (
        <Box role="status" aria-label="正在加载规则数据" sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mt: 3 }}>
          <CircularProgress size={20} />
          <Typography color="text.secondary">正在读取板子与角色数据…</Typography>
        </Box>
      )}

      {!loading && (
        <>
          <Section title="板子与对局配置" intro="标准场的角色构成与开关；创建对局时也可以自行调整人数与角色数量。">
            {selected ? (
              <Paper variant="outlined" sx={{ p: 2, borderColor: HAIRLINE.soft }}>
                <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 1 }}>
                  {presets.map((preset) => (
                    <Button
                      key={preset.id}
                      variant={preset.id === selected.id ? 'contained' : 'outlined'}
                      color={preset.id === selected.id ? 'primary' : 'inherit'}
                      aria-pressed={preset.id === selected.id}
                      onClick={() => setPresetId(preset.id)}
                      sx={{
                        flexDirection: 'column', alignItems: 'flex-start',
                        px: 1.5, py: 0.75, lineHeight: 1.35, textAlign: 'left',
                      }}
                    >
                      <Typography component="span" variant="subtitle2">{preset.name}</Typography>
                      <Typography component="span" variant="caption" sx={{ opacity: 0.85 }}>
                        {preset.description}
                      </Typography>
                    </Button>
                  ))}
                </Stack>

                <Stack direction="row" sx={{ flexWrap: 'wrap', alignItems: 'center', gap: 1, mt: 2 }}>
                  <Typography variant="subtitle1">{selected.name}</Typography>
                  <Chip
                    size="small"
                    variant="outlined"
                    color={selected.enable_sheriff ? 'secondary' : 'default'}
                    label={selected.enable_sheriff ? '警长：开启' : '警长：关闭'}
                  />
                  <Chip size="small" variant="outlined" label={`${total} 人局`} />
                </Stack>

                <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 1, mt: 1.5 }}>
                  {composition.map(([roleId, count]) => (
                    <Stack
                      key={roleId}
                      direction="row"
                      spacing={0.5}
                      sx={{
                        alignItems: 'center', px: 1, py: 0.5,
                        border: '1px solid', borderColor: HAIRLINE.soft, borderRadius: 1,
                      }}
                    >
                      <RoleIcon role={roleId} size={18} />
                      <Typography variant="body2">{nameOf(roleId)}</Typography>
                      <Typography variant="body2" color="text.secondary">×{count}</Typography>
                    </Stack>
                  ))}
                </Stack>

                {constraints && (
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
                    自定义板子支持 {constraints.min_players}–{constraints.max_players} 人，至少
                    {' '}{constraints.min_werewolves} 名狼人、{constraints.min_good} 名好人。
                  </Typography>
                )}
              </Paper>
            ) : (
              <Typography variant="body2" color="text.secondary">
                {error ? '板子数据读取失败，下方通用规则不受影响。' : '暂时没有可展示的板子数据。'}
              </Typography>
            )}
          </Section>

          <Section title="角色能力" intro="角色与规则条目来自后端角色目录，新增角色会自动出现在这里。">
            {groups.length === 0 ? (
              <Typography variant="body2" color="text.secondary">
                {error ? '角色数据读取失败，下方通用规则不受影响。' : '暂时没有可展示的角色数据。'}
              </Typography>
            ) : groups.map((group) => (
              <Box key={group.camp} sx={{ mt: 1.5 }}>
                <Typography variant="subtitle1" sx={{ color: 'secondary.main' }}>
                  {campLabel(group.camp)}
                </Typography>
                <Box
                  sx={{
                    mt: 1, display: 'grid', gap: 1.5,
                    gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
                  }}
                >
                  {group.roles.map((role) => (
                    <Paper
                      key={role.role_id}
                      variant="outlined"
                      data-testid={`role-card-${role.role_id}`}
                      sx={{ p: 1.5, borderColor: HAIRLINE.soft }}
                    >
                      <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                        <RoleIcon role={role.role_id} size={22} />
                        <Typography
                          variant="subtitle1"
                          sx={{ color: roleMetaFor(role.role_id)?.color ?? 'text.primary' }}
                        >
                          {role.name_zh}
                        </Typography>
                      </Stack>
                      {(role.rules ?? []).length > 0 ? (
                        <Box sx={{ mt: 0.5 }}>
                          <RuleList items={role.rules ?? []} />
                        </Box>
                      ) : (
                        <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
                          {role.description}
                        </Typography>
                      )}
                    </Paper>
                  ))}
                </Box>
              </Box>
            ))}
          </Section>

          {RULE_SECTIONS.map((section: RuleSection) => (
            <Section key={section.id} title={section.title} intro={section.intro}>
              <RuleList items={section.items} ordered={section.ordered} />
            </Section>
          ))}
        </>
      )}
    </Container>
  );
}
