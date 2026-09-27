import { Box, Typography } from '@mui/material';

import type { SeatModelInfo } from '../../store/seatModels';
import { providerProfileLabel } from '../models/providerProfiles';
import { BACKDROP, BLOOD_MOON, CANVAS, HAIRLINE, INK } from '../../theme/tokens';

function campLabel(camp?: string): string | undefined {
  if (camp === 'werewolf') return '狼人阵营';
  if (camp === 'good') return '好人阵营';
  return undefined;
}

function statusLine(options: {
  isAlive: boolean;
  canVote?: boolean;
  isSheriff: boolean;
  isCurrentSpeaker: boolean;
  voteTarget?: number | null;
  badgeSide?: 'on' | 'off';
}): string {
  const parts = [options.isAlive ? '存活' : '出局'];
  if (options.isAlive && options.canVote === false) parts.push('无投票权');
  if (options.badgeSide === 'on') parts.push('警上');
  if (options.badgeSide === 'off') parts.push('警下');
  if (options.isSheriff) parts.push('警长');
  if (options.isCurrentSpeaker) parts.push('发言中');
  if (options.voteTarget !== undefined) {
    parts.push(options.voteTarget === null ? '弃权' : `→ ${options.voteTarget}号`);
  }
  return parts.join(' · ');
}

export default function SeatHoverCard({
  seat,
  roman,
  roleLabel,
  camp,
  isAlive,
  canVote,
  isSheriff,
  isCurrentSpeaker,
  voteTarget,
  badgeSide,
  accent,
  model,
  art,
}: {
  seat: number;
  roman: string;
  roleLabel?: string;
  camp?: string;
  isAlive: boolean;
  canVote?: boolean;
  isSheriff: boolean;
  isCurrentSpeaker: boolean;
  voteTarget?: number | null;
  badgeSide?: 'on' | 'off';
  accent: string;
  model?: SeatModelInfo;
  art?: string;
}) {
  const identity = [roleLabel, campLabel(camp)].filter(Boolean).join(' · ');
  return (
    <Box
      sx={{
        minWidth: 188,
        maxWidth: 260,
        px: 1.5,
        py: 1.25,
        borderRadius: 1.5,
        bgcolor: CANVAS.surfaceRaised,
        border: `1px solid ${HAIRLINE.strong}`,
        boxShadow: `0 12px 28px rgba(0,0,0,0.55), 0 0 0 1px ${accent}33`,
        pointerEvents: 'none',
      }}
    >
      {art ? (
        <Box sx={{ position: 'relative', mx: -1.5, mt: -1.25, mb: 1, overflow: 'hidden' }}>
          <Box
            component="img"
            alt=""
            src={art}
            sx={{ display: 'block', width: '100%', height: 108, objectFit: 'cover', objectPosition: 'center 15%' }}
          />
          <Box sx={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 42, background: BACKDROP.cardScrim }} />
        </Box>
      ) : null}
      <Typography
        sx={{
          fontFamily: '"Cinzel","Noto Serif SC",serif',
          fontWeight: 800,
          fontSize: '0.78rem',
          letterSpacing: 1.5,
          color: INK.primary,
        }}
      >
        {roman} · {seat}号
      </Typography>
      {identity ? (
        <Typography
          sx={{
            mt: 0.25,
            fontSize: '0.72rem',
            fontWeight: 700,
            letterSpacing: 1,
            color: accent,
          }}
        >
          {identity}
        </Typography>
      ) : null}
      <Box sx={{ my: 1, height: 1, bgcolor: HAIRLINE.soft }} />
      <HoverRow label="状态" value={statusLine({
        isAlive, canVote, isSheriff, isCurrentSpeaker, voteTarget, badgeSide,
      })} />
      <HoverRow label="模型" value={model?.name ?? '未知'} emphasize />
      {model ? (
        <>
          <Typography sx={{ mt: 0.15, pl: 4.5, fontSize: '0.66rem', color: INK.dim }}>
            {model.model_id}
          </Typography>
          <Typography sx={{ pl: 4.5, fontSize: '0.62rem', color: INK.faint }}>
            {providerProfileLabel(model.provider_profile)}
          </Typography>
        </>
      ) : null}
    </Box>
  );
}

function HoverRow({
  label,
  value,
  emphasize = false,
}: {
  label: string;
  value: string;
  emphasize?: boolean;
}) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, mt: 0.35 }}>
      <Typography
        sx={{
          width: 28,
          flexShrink: 0,
          fontSize: '0.62rem',
          letterSpacing: 1.5,
          color: BLOOD_MOON.gold,
        }}
      >
        {label}
      </Typography>
      <Typography
        sx={{
          fontSize: emphasize ? '0.78rem' : '0.7rem',
          fontWeight: emphasize ? 700 : 500,
          color: INK.primary,
        }}
      >
        {value}
      </Typography>
    </Box>
  );
}
