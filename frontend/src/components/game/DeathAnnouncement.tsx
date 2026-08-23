import Box from '@mui/material/Box';
import Dialog from '@mui/material/Dialog';
import DialogContent from '@mui/material/DialogContent';
import Typography from '@mui/material/Typography';
import Bloodtype from '@mui/icons-material/Bloodtype';
import Gavel from '@mui/icons-material/Gavel';
import GpsFixed from '@mui/icons-material/GpsFixed';
import PersonOff from '@mui/icons-material/PersonOff';
import Science from '@mui/icons-material/Science';
import type { SvgIconComponent } from '@mui/icons-material';
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

const CAUSE_ICONS: Record<string, SvgIconComponent> = {
  wolf_kill: Bloodtype,
  poison: Science,
  hunter_shot: GpsFixed,
  exile: Gavel,
};

export default function DeathAnnouncement({ deaths }: Props) {
  const latest = deaths[deaths.length - 1];
  if (!latest) return null;

  const causeLabel = CAUSE_LABELS[latest.cause] || latest.cause;
  const Icon = CAUSE_ICONS[latest.cause] ?? PersonOff;

  return (
    <Dialog open maxWidth="xs" fullWidth aria-label="死亡公告">
      <DialogContent sx={{ textAlign: 'center', py: 4, px: 3 }}>
        <Box
          aria-hidden="true"
          sx={{
            width: 64,
            height: 64,
            mx: 'auto',
            mb: 1.5,
            borderRadius: '50%',
            display: 'grid',
            placeItems: 'center',
            color: '#F6686C',
            bgcolor: 'rgba(229,72,77,0.12)',
            border: '1px solid rgba(229,72,77,0.35)',
            boxShadow: '0 0 24px rgba(229,72,77,0.25)',
          }}
        >
          <Icon sx={{ fontSize: 30 }} />
        </Box>
        <Typography variant="h5" gutterBottom sx={{ fontWeight: 900, letterSpacing: 3 }}>
          {latest.player_seat}号玩家出局
        </Typography>
        <Typography variant="body1" color="text.secondary">
          {causeLabel} · 第{latest.round_number}轮
        </Typography>
      </DialogContent>
    </Dialog>
  );
}
