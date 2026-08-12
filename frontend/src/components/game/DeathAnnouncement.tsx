import { Dialog, DialogContent, Typography } from '@mui/material';
import type { DeathRecord } from '../../store/types';

interface Props {
  deaths: DeathRecord[];
}

const CAUSE_LABELS: Record<string, string> = {
  wolf_kill: '夜间死亡',
  poison: '毒杀',
  exile: '放逐',
  hunter_shot: '猎人带走',
};

export default function DeathAnnouncement({ deaths }: Props) {
  const latest = deaths[deaths.length - 1];
  if (!latest) return null;

  const causeLabel = CAUSE_LABELS[latest.cause] || latest.cause;
  const icon = latest.cause === 'wolf_kill' ? '☠' : '✦';

  return (
    <Dialog open maxWidth="xs" fullWidth>
      <DialogContent sx={{ textAlign: 'center', py: 4, px: 3 }}>
        <Typography sx={{ fontSize: '2.5rem', lineHeight: 1, mb: 1.5 }}>{icon}</Typography>
        <Typography variant="h5" sx={{ fontWeight: 400 }} gutterBottom>
          {latest.player_seat}号玩家出局
        </Typography>
        <Typography variant="body1" color="text.secondary">
          {causeLabel} · 第{latest.round_number}轮
        </Typography>
      </DialogContent>
    </Dialog>
  );
}
