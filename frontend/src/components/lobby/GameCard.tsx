import { useState } from 'react';
import { Card, CardActionArea, Typography, Chip, Box, IconButton, Menu, MenuItem } from '@mui/material';
import GroupsIcon from '@mui/icons-material/Groups';
import MoreVertIcon from '@mui/icons-material/MoreVert';

interface Props {
  gameId: string;
  name: string;
  phase: string;
  roundNumber: number;
  playerCount: number;
  aliveCount: number;
  winner: string | null;
  onClick: () => void;
  onRename: () => void;
  onDelete: () => void;
}

const WINNER_META: Record<string, { label: string; color: 'success' | 'error' }> = {
  good: { label: '好人胜', color: 'success' },
  werewolf: { label: '狼人胜', color: 'error' },
};

const PHASE_LABELS: Record<string, string> = {
  error: '异常终止',
};

export default function GameCard({
  name, phase, roundNumber, playerCount, aliveCount, winner, onClick, onRename, onDelete,
}: Props) {
  const win = winner ? WINNER_META[winner] : null;
  const [menuEl, setMenuEl] = useState<null | HTMLElement>(null);

  return (
    <Card
      variant="outlined"
      sx={{
        transition: 'background-color 0.2s, border-color 0.2s',
        '&:hover': {
          bgcolor: 'action.hover',
          borderColor: 'primary.main',
        },
      }}
    >
      <Box sx={{ display: 'flex', alignItems: 'stretch' }}>
        <CardActionArea onClick={onClick} sx={{ p: 0, flex: 1 }}>
          <Box sx={{ px: 2.5, py: 2 }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
              <Box>
                <Typography variant="subtitle1" sx={{ fontWeight: 500, fontSize: '0.9rem' }}>
                  {name}
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3 }}>
                  {PHASE_LABELS[phase] ?? phase} · 第 {roundNumber} 轮
                </Typography>
              </Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <GroupsIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
                  <Typography variant="body2" color="text.secondary">
                    {aliveCount}/{playerCount}
                  </Typography>
                </Box>
                {win && (
                  <Chip
                    label={win.label}
                    color={win.color}
                    size="small"
                    variant="outlined"
                    sx={{ fontWeight: 500, height: 24, fontSize: '0.7rem' }}
                  />
                )}
              </Box>
            </Box>
          </Box>
        </CardActionArea>
        <Box sx={{ display: 'flex', alignItems: 'center', pr: 1 }}>
          <IconButton
            aria-label="对局操作"
            onClick={(event) => setMenuEl(event.currentTarget)}
          >
            <MoreVertIcon />
          </IconButton>
          <Menu
            anchorEl={menuEl}
            open={Boolean(menuEl)}
            onClose={() => setMenuEl(null)}
          >
            <MenuItem onClick={() => { setMenuEl(null); onRename(); }}>重命名</MenuItem>
            <MenuItem onClick={() => { setMenuEl(null); onDelete(); }}>删除</MenuItem>
          </Menu>
        </Box>
      </Box>
    </Card>
  );
}
