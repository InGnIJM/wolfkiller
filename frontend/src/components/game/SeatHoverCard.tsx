import { Box, Typography } from '@mui/material';

import type { SeatModelInfo } from '../../store/seatModels';
import RolePortrait from '../shared/RolePortrait';
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
  roleId,
  description,
  rules,
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
  /** 已揭晓的身份；缺省（旧档 / 未揭晓）由 `RolePortrait` 回退到卡背。 */
  roleId?: string;
  /** 目录里的一句技能简介；未揭晓或缺目录时不传。 */
  description?: string;
  /** 目录里的规则条目；未揭晓或缺目录时不传。 */
  rules?: readonly string[];
}) {
  const identity = [roleLabel, campLabel(camp)].filter(Boolean).join(' · ');
  const skillRules = roleId ? (rules ?? []).filter((rule) => rule.trim().length > 0) : [];
  const skillDescription = roleId ? description?.trim() : '';
  const showSkills = Boolean(skillDescription || skillRules.length > 0);
  return (
    <Box
      sx={{
        minWidth: 188,
        maxWidth: 300,
        px: 1.5,
        py: 1.25,
        borderRadius: 1.5,
        bgcolor: CANVAS.surfaceRaised,
        border: `1px solid ${HAIRLINE.strong}`,
        boxShadow: `0 12px 28px rgba(0,0,0,0.55), 0 0 0 1px ${accent}33`,
        pointerEvents: 'auto',
      }}
    >
      <Box sx={{ position: 'relative', mx: -1.5, mt: -1.25, mb: 1, overflow: 'hidden' }}>
        <RolePortrait roleId={roleId} height={108} showLabel={false} />
        <Box sx={{ position: 'absolute', left: 0, right: 0, bottom: 0, height: 42, background: BACKDROP.cardScrim }} />
      </Box>
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
      {showSkills ? (
        <>
          {skillDescription ? <HoverRow label="技能" value={skillDescription} /> : null}
          {skillRules.length > 0 ? (
            <Box
              component="ul"
              data-testid="seat-hover-rules"
              sx={{
                m: 0,
                mt: 0.5,
                pl: 2,
                maxHeight: 140,
                overflowY: 'auto',
              }}
            >
              {skillRules.map((rule) => (
                <Typography
                  key={rule}
                  component="li"
                  sx={{
                    fontSize: '0.62rem',
                    color: INK.dim,
                    lineHeight: 1.5,
                    mb: 0.4,
                    pr: 0.5,
                  }}
                >
                  {rule}
                </Typography>
              ))}
            </Box>
          ) : null}
        </>
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
          lineHeight: 1.45,
        }}
      >
        {value}
      </Typography>
    </Box>
  );
}
