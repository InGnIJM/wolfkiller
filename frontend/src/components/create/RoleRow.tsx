import { Box, Chip, IconButton, Stack, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import RemoveIcon from '@mui/icons-material/Remove';

import { WINNING_CAMP_LABELS } from '../../labels';
import type { RoleCatalogItem, WinningCamp } from '../../store/types';
import { BLOOD_MOON, CANVAS, HAIRLINE, INK } from '../../theme/tokens';
import RolePortrait from '../shared/RolePortrait';

interface Props {
  role: RoleCatalogItem;
  count: number;
  /** 这个角色不在原板子里，是后来加的 —— 描边与角标都要标出来。 */
  added?: boolean;
  canIncrement: boolean;
  canDecrement: boolean;
  onChange: (delta: number) => void;
}

/** 一行一个角色：立绘 + 名字 + 描述 + 阵营 + 加减号。沿用改造前的行式样式。 */
export default function RoleRow({
  role, count, added = false, canIncrement, canDecrement, onChange,
}: Props) {
  const wolf = role.camp === 'werewolf';
  const campColor = wolf ? BLOOD_MOON.crimson : INK.dim;
  const campText = role.camp in WINNING_CAMP_LABELS
    ? WINNING_CAMP_LABELS[role.camp as WinningCamp]
    : role.camp;

  return (
    <Box
      sx={{
        display: 'flex', alignItems: 'stretch', overflow: 'hidden',
        bgcolor: 'background.paper', borderRadius: 1,
        border: `1px solid ${added ? BLOOD_MOON.gold : HAIRLINE.soft}`,
        opacity: count === 0 ? 0.55 : 1,
        transition: (theme) => theme.transitions.create('background-color', {
          duration: theme.transitions.duration.shorter,
        }),
        '&:hover': { bgcolor: CANVAS.surfaceRaised },
      }}
    >
      <RolePortrait roleId={role.role_id} width={72} />

      <Box
        sx={{
          flex: 1, minWidth: 0, display: 'flex', alignItems: 'center',
          justifyContent: 'space-between', gap: 1.5, px: 1.75, py: 1,
        }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Stack direction="row" spacing={0.75} sx={{ alignItems: 'center' }}>
            <Typography variant="body2" sx={{ fontWeight: 700, lineHeight: 1.35 }}>
              {role.name_zh}
            </Typography>
            {added && (
              <Box
                component="span"
                sx={{
                  fontSize: 9, lineHeight: '14px', px: 0.5, borderRadius: 0.5,
                  bgcolor: BLOOD_MOON.gold, color: CANVAS.bg, fontWeight: 700,
                }}
              >
                新增
              </Box>
            )}
          </Stack>
          <Typography
            variant="caption"
            sx={{
              display: 'block', color: INK.dim, lineHeight: 1.4,
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}
          >
            {role.description}
          </Typography>
        </Box>

        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexShrink: 0 }}>
          <Chip
            size="small"
            variant="outlined"
            label={campText}
            sx={{ borderColor: campColor, color: campColor }}
          />
          <IconButton
            size="small"
            aria-label={`减少${role.name_zh}`}
            disabled={!canDecrement}
            onClick={() => onChange(-1)}
          >
            <RemoveIcon fontSize="small" />
          </IconButton>
          <Typography
            sx={{
              width: 26, textAlign: 'center', fontWeight: 700,
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {count}
          </Typography>
          <IconButton
            size="small"
            aria-label={`增加${role.name_zh}`}
            disabled={!canIncrement}
            onClick={() => onChange(1)}
          >
            <AddIcon fontSize="small" />
          </IconButton>
        </Stack>
      </Box>
    </Box>
  );
}
