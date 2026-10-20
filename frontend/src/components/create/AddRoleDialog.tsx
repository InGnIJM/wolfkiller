import { useEffect, useMemo, useState } from 'react';
import {
  Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle,
  IconButton, InputAdornment, Stack, TextField, Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import SearchIcon from '@mui/icons-material/Search';

import { WINNING_CAMP_LABELS } from '../../labels';
import type { RoleCatalogItem, WinningCamp } from '../../store/types';
import { BLOOD_MOON, HAIRLINE, INK } from '../../theme/tokens';
import RolePortrait from '../shared/RolePortrait';
import { rankOf } from '../shared/roleMeta';

const SEARCH_DEBOUNCE_MS = 200;

function campLabel(camp: string): string {
  return camp in WINNING_CAMP_LABELS
    ? WINNING_CAMP_LABELS[camp as WinningCamp]
    : camp;
}

interface Props {
  open: boolean;
  /** 当前板子里没有的角色（数量为 0 的那些）。 */
  candidates: readonly RoleCatalogItem[];
  onClose: () => void;
  onConfirm: (roleIds: string[]) => void;
}

/**
 * 「+ 添加角色」。只列当前板子里没有的角色，按阵营分组，可搜索、可多选。
 *
 * 关闭不保留已选：留着会让人以为已经加了。
 */
export default function AddRoleDialog({
  open, candidates, onClose, onConfirm,
}: Props) {
  const [rawQuery, setRawQuery] = useState('');
  const [query, setQuery] = useState('');
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());

  useEffect(() => {
    const timer = setTimeout(() => setQuery(rawQuery), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [rawQuery]);

  // 每次打开都从干净状态开始，避免上一次的选择残留。用「渲染期调整 state」而不是
  // effect：effect 里同步 setState 会多跑一轮渲染，react-hooks 规则也会拦。
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setRawQuery('');
      setQuery('');
      setPicked(new Set());
    }
  }

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const visible = needle
      ? candidates.filter((role) => (
        role.name_zh.toLowerCase().includes(needle)
        || role.description.toLowerCase().includes(needle)
      ))
      : candidates;
    const byCamp = new Map<string, RoleCatalogItem[]>();
    for (const role of [...visible].sort(
      (a, b) => rankOf(a.role_id) - rankOf(b.role_id),
    )) {
      byCamp.set(role.camp, [...(byCamp.get(role.camp) ?? []), role]);
    }
    return [...byCamp.entries()];
  }, [candidates, query]);

  const toggle = (roleId: string) => {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(roleId)) next.delete(roleId);
      else next.add(roleId);
      return next;
    });
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth aria-labelledby="add-role-title">
      <DialogTitle id="add-role-title" sx={{ pb: 0.5 }}>
        添加角色
        <IconButton
          size="small"
          aria-label="关闭"
          onClick={onClose}
          sx={{ position: 'absolute', right: 12, top: 12 }}
        >
          <CloseIcon fontSize="small" />
        </IconButton>
        <Typography variant="caption" sx={{ display: 'block', color: INK.faint, mt: 0.5 }}>
          已选中 {picked.size} 个 · 当前板子已有 {candidates.length === 0 ? '全部' : ''} 角色不再列出
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ pt: 1 }}>
        <TextField
          value={rawQuery}
          onChange={(event) => setRawQuery(event.target.value)}
          placeholder="搜索角色…"
          size="small"
          fullWidth
          slotProps={{
            htmlInput: { 'aria-label': '搜索角色' },
            input: {
              startAdornment: (
                <InputAdornment position="start">
                  <SearchIcon sx={{ fontSize: 18, color: INK.faint }} />
                </InputAdornment>
              ),
            },
          }}
        />

        <Box sx={{ mt: 1.5, maxHeight: 320, overflowY: 'auto' }}>
          {groups.length === 0 && (
            <Box sx={{ py: 3, textAlign: 'center' }}>
              <Typography variant="body2" sx={{ color: INK.dim }}>
                没有可添加的角色
              </Typography>
              <Typography variant="caption" sx={{ color: INK.faint }}>
                {candidates.length === 0
                  ? '全部角色都已经在这个板子里了'
                  : '换个关键词试试'}
              </Typography>
            </Box>
          )}

          {groups.map(([camp, items]) => (
            <Box key={camp}>
              <Typography
                variant="caption"
                sx={{
                  display: 'block', py: 0.75, color: BLOOD_MOON.gold,
                  letterSpacing: '0.08em', fontWeight: 600,
                }}
              >
                {campLabel(camp)} · {items.length}
              </Typography>
              <Stack spacing={0.5}>
                {items.map((role) => {
                  const on = picked.has(role.role_id);
                  return (
                    <Box
                      key={role.role_id}
                      component="label"
                      sx={{
                        display: 'flex', alignItems: 'center', gap: 1,
                        px: 1, py: 0.5, borderRadius: 1, cursor: 'pointer',
                        minHeight: 44,
                        border: `1px solid ${on ? BLOOD_MOON.gold : HAIRLINE.soft}`,
                        bgcolor: on ? 'rgba(212, 168, 83, 0.08)' : 'transparent',
                      }}
                    >
                      <Checkbox
                        size="small"
                        checked={on}
                        onChange={() => toggle(role.role_id)}
                        slotProps={{ input: { 'aria-label': role.name_zh } }}
                        sx={{ p: 0.5 }}
                      />
                      <RolePortrait
                        roleId={role.role_id}
                        width={28}
                        height={36}
                        showLabel={false}
                      />
                      <Box sx={{ minWidth: 0 }}>
                        <Typography variant="body2" sx={{ color: on ? INK.primary : INK.dim }}>
                          {role.name_zh}
                        </Typography>
                      </Box>
                    </Box>
                  );
                })}
              </Stack>
            </Box>
          ))}
        </Box>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        <Button color="inherit" onClick={onClose}>取消</Button>
        <Button
          variant="contained"
          disableElevation
          disabled={picked.size === 0}
          onClick={() => onConfirm([...picked])}
        >
          添加 {picked.size} 个角色
        </Button>
      </DialogActions>
    </Dialog>
  );
}
