import { Dialog, DialogContent, Typography, Box } from '@mui/material';
import { DeathRecord } from '../../store/types';
import RoleIcon from '../shared/RoleIcon';

interface Props {
  deaths: DeathRecord[];
  playerRoles: Record<number, string | null>;
}

const CAUSE_LABELS: Record<string, string> = {
  wolf_kill: '被狼人杀害',
  poison: '被毒杀',
  exile: '被放逐',
  hunter_shot: '被猎人带走',
};

export default function DeathAnnouncement({ deaths, playerRoles }: Props) {
  const latest = deaths[deaths.length - 1];
  if (!latest) return null;

  const role = playerRoles[latest.player_seat];
  const causeLabel = CAUSE_LABELS[latest.cause] || latest.cause;
  const icon = latest.cause === 'wolf_kill' ? '☠' : '⚰';

  return (
    <Dialog open={true} maxWidth="xs" fullWidth>
      <DialogContent sx={{ textAlign: 'center', py: 4, px: 3 }}>
        <Typography sx={{ fontSize: '2.5rem', lineHeight: 1, mb: 1.5 }}>{icon}</Typography>
        <Typography variant="h5" fontWeight={400} gutterBottom>
          {latest.player_seat}号玩家
        </Typography>
        {role && (
          <Box sx={{ my: 1.5 }}>
            <RoleIcon role={role} size={40} />
          </Box>
        )}
        <Typography variant="body1" color="text.secondary">
          因{causeLabel}出局
        </Typography>
        {role && (
          <Typography variant="body2" sx={{ mt: 1.5, color: 'primary.light', fontWeight: 500 }}>
            身份：{role}
          </Typography>
        )}
      </DialogContent>
    </Dialog>
  );
}
