import Box from '@mui/material/Box';
import Dialog from '@mui/material/Dialog';
import DialogContent from '@mui/material/DialogContent';
import Typography from '@mui/material/Typography';
import Bloodtype from '@mui/icons-material/Bloodtype';
import Bolt from '@mui/icons-material/Bolt';
import Gavel from '@mui/icons-material/Gavel';
import GpsFixed from '@mui/icons-material/GpsFixed';
import HeartBroken from '@mui/icons-material/HeartBroken';
import PersonOff from '@mui/icons-material/PersonOff';
import Pets from '@mui/icons-material/Pets';
import Nightlight from '@mui/icons-material/Nightlight';
import Replay from '@mui/icons-material/Replay';
import Science from '@mui/icons-material/Science';
import Whatshot from '@mui/icons-material/Whatshot';
import type { SvgIconComponent } from '@mui/icons-material';
import type { DeathRecord } from '../../store/types';
import RolePortrait from '../shared/RolePortrait';
import { useTranslation } from 'react-i18next';
import { useLabels } from '../../i18n/useLabels';
import { BACKDROP, BLOOD_MOON, HAIRLINE, ROLE_COLORS } from '../../theme/tokens';

interface Props {
  deaths: DeathRecord[];
  revealedRole?: string | null;
}

const CAUSE_ICONS: Record<string, SvgIconComponent> = {
  wolf_kill: Bloodtype,
  poison: Science,
  hunter_shot: GpsFixed,
  exile: Gavel,
  self_explode: Whatshot,
  knight_duel: Bolt,
  charm: HeartBroken,
  wolf_king_shot: Pets,
  reflect: Replay,
  dream_death: Nightlight,
};

export default function DeathAnnouncement({ deaths, revealedRole }: Props) {
  const { t } = useTranslation();
  const L = useLabels();
  const latest = deaths[deaths.length - 1];
  if (!latest) return null;

  const causeLabel = L.DEATH_CAUSE_LABELS[latest.cause] || latest.cause;
  const Icon = CAUSE_ICONS[latest.cause] ?? PersonOff;
  const roleLabel = L.roleName(revealedRole) ?? revealedRole ?? null;

  return (
    <Dialog open maxWidth="xs" fullWidth aria-label={t('game.deathAnnouncement')}>
      <DialogContent sx={{ textAlign: 'center', py: 4, px: 3 }}>
        <Box
          aria-hidden="true"
          sx={{
            position: 'relative', width: 88, mx: 'auto', mb: 1.5, borderRadius: 1, overflow: 'hidden',
            border: `1px solid ${HAIRLINE.strong}`,
          }}
        >
          <RolePortrait roleId={revealedRole} width={86} height={116} />
          <Box sx={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: '46%', background: BACKDROP.cardScrim }} />
        </Box>
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
            color: ROLE_COLORS.werewolf.color,
            bgcolor: 'rgba(201,86,107,0.12)',
            border: '1px solid rgba(201,86,107,0.35)',
          }}
        >
          <Icon sx={{ fontSize: 30 }} />
        </Box>
        <Typography variant="h5" gutterBottom sx={{ fontWeight: 700, letterSpacing: 3 }}>
          {t('game.playerOut', { seat: latest.player_seat })}
        </Typography>
        {roleLabel && (
          <Typography variant="body1" sx={{ fontWeight: 600, color: BLOOD_MOON.gold }}>
            {t('game.revealedRole', { role: roleLabel })}
          </Typography>
        )}
        <Typography variant="body1" color="text.secondary">
          {t('game.deathRound', { cause: causeLabel, round: latest.round_number })}
        </Typography>
      </DialogContent>
    </Dialog>
  );
}
