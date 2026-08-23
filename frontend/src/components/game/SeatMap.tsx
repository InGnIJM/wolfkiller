import { Box, Typography, keyframes } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { useEffect, useRef, useState } from 'react';
import type { PublicPlayerState } from '../../store/types';
import { ROLE_COLORS } from '../../theme/tokens';

const speakerPulse = keyframes`
  0%, 100% { boxShadow: '0 0 0 2px rgba(229,72,77,0.55), 0 0 10px rgba(229,72,77,0.3)'; }
  50% { boxShadow: '0 0 0 6px rgba(229,72,77,0.28), 0 0 20px rgba(229,72,77,0.16)'; }
`;

const ROLE_BADGES: Record<string, { label: string; color: string; bg: string }> = {
  'wolf-killer-werewolf': { label: '狼人', ...ROLE_COLORS.werewolf },
  'wolf-killer-witch': { label: '女巫', ...ROLE_COLORS.witch },
  'wolf-killer-seer': { label: '预言家', ...ROLE_COLORS.seer },
  'wolf-killer-hunter': { label: '猎人', ...ROLE_COLORS.hunter },
  'wolf-killer-villager': { label: '村民', ...ROLE_COLORS.villager },
  'wolf-killer-guard': { label: '守卫', ...ROLE_COLORS.guard },
};

interface Props {
  players: Record<number, PublicPlayerState>;
  currentSpeaker?: number | null;
  voteTargets?: Record<number, number | null>;
  children?: React.ReactNode;
}

function getRectPosition(index: number, total: number, rectW: number, rectH: number) {
  if (total <= 1) return { x: rectW / 2, y: rectH / 2 };

  const perimeter = 2 * (rectW + rectH);
  const dist = index * (perimeter / total);
  if (dist < rectW) return { x: dist, y: 0 };

  let remaining = dist - rectW;
  if (remaining < rectH) return { x: rectW, y: remaining };
  remaining -= rectH;
  if (remaining < rectW) return { x: rectW - remaining, y: rectH };
  return { x: 0, y: rectH - (remaining - rectW) };
}

function PublicSeat({
  seat,
  player,
  isCurrentSpeaker,
  voteTarget,
  cardSize,
}: {
  seat: number;
  player: PublicPlayerState;
  isCurrentSpeaker: boolean;
  voteTarget: number | null | undefined;
  cardSize: number;
}) {
  const badge = player.role ? ROLE_BADGES[player.role] : undefined;
  const status = player.is_alive ? '存活' : '出局';
  const label = [
    `${seat}号`,
    badge?.label,
    status,
    player.is_sheriff ? '警长' : '',
  ].filter(Boolean).join(' ');
  return (
    <Box
      aria-label={label}
      sx={{
        position: 'relative',
        display: 'flex',
        minWidth: cardSize,
        flexDirection: 'column',
        alignItems: 'center',
        gap: 0.25,
        p: 0.7,
        pt: 0.9,
        border: '1px solid',
        borderTop: '2px solid',
        borderColor: isCurrentSpeaker ? 'error.main' : (
          badge?.color
          ?? (player.camp === 'werewolf' ? 'error.main' : player.camp === 'good' ? 'success.main' : 'divider')
        ),
        borderTopColor: isCurrentSpeaker ? 'error.main' : (badge?.color ?? 'divider'),
        borderRadius: 1.5,
        bgcolor: player.is_alive ? 'rgba(23,18,33,0.85)' : 'action.disabledBackground',
        opacity: player.is_alive ? 1 : 0.55,
        boxShadow: '0 6px 18px rgba(0,0,0,0.35)',
        ...(isCurrentSpeaker ? { animation: `${speakerPulse} 1.2s ease-in-out infinite` } : {}),
      }}
    >
      {player.is_sheriff && (
        <Typography
          variant="caption"
          sx={{
            position: 'absolute',
            top: -9,
            left: '50%',
            transform: 'translateX(-50%)',
            color: 'background.paper',
            bgcolor: 'warning.main',
            borderRadius: 99,
            px: 0.9,
            py: 0.1,
            fontSize: '0.58rem',
            fontWeight: 800,
            letterSpacing: 1,
            lineHeight: 1.4,
            boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
            whiteSpace: 'nowrap',
          }}
        >
          警长
        </Typography>
      )}
      {!player.is_alive && (
        <Box
          aria-hidden="true"
          sx={{
            position: 'absolute',
            top: -8,
            right: -8,
            width: 20,
            height: 20,
            borderRadius: '50%',
            display: 'grid',
            placeItems: 'center',
            color: 'background.paper',
            bgcolor: 'error.main',
            boxShadow: '0 0 10px rgba(229,72,77,0.6)',
          }}
        >
          <CloseIcon sx={{ fontSize: 13 }} />
        </Box>
      )}
      <Typography
        variant="caption"
        sx={{
          fontWeight: 800,
          fontSize: '0.78rem',
          lineHeight: 1.2,
          letterSpacing: 1,
          fontFamily: '"Cinzel","Noto Serif SC",serif',
          color: isCurrentSpeaker ? 'error.light' : 'text.primary',
        }}
      >
        {seat}号
      </Typography>
      {badge && (
        <Typography
          component="span"
          sx={{
            fontWeight: 700,
            fontSize: '0.72rem',
            lineHeight: 1.3,
            px: 0.7,
            py: 0.15,
            borderRadius: 1,
            color: badge.color,
            bgcolor: badge.bg,
            border: '1px solid',
            borderColor: badge.color,
          }}
        >
          {badge.label}
        </Typography>
      )}
      <Typography variant="caption" color={player.is_alive ? 'success.light' : 'text.disabled'}>
        {status}
      </Typography>
      {voteTarget !== undefined && (
        <Typography variant="caption" color={voteTarget === null ? 'text.disabled' : 'warning.light'}>
          {voteTarget === null ? '弃权' : `→ ${voteTarget}号`}
        </Typography>
      )}
    </Box>
  );
}

export default function SeatMap({ players, currentSpeaker, voteTargets, children }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return undefined;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      const nextSize = { w: entry.contentRect.width, h: entry.contentRect.height };
      setSize((previous) => (
        Math.abs(previous.w - nextSize.w) > 2 || Math.abs(previous.h - nextSize.h) > 2
          ? nextSize
          : previous
      ));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const seats = Object.entries(players)
    .map(([seat, player]) => ({ seat: Number(seat), player }))
    .sort((left, right) => left.seat - right.seat);
  const cardSize = size.w > 0 ? Math.max(44, Math.min(72, Math.min(size.w, size.h) * 0.1)) : 52;
  const padX = Math.max(60, size.w * 0.1);
  const padY = Math.max(50, size.h * 0.1);
  const rectW = Math.max(100, size.w - padX * 2);
  const rectH = Math.max(100, size.h - padY * 2);

  return (
    <Box ref={containerRef} sx={{ position: 'relative', width: '100%', height: '100%', minHeight: 420, overflow: 'hidden' }}>
      {size.w > 0 && (
        <Box
          aria-hidden="true"
          sx={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            transform: 'translate(-50%,-50%)',
            width: Math.min(rectW * 0.94, 600),
            height: rectH * 0.84,
            border: '1px dashed',
            borderColor: 'divider',
            borderRadius: '50%',
            opacity: 0.55,
            pointerEvents: 'none',
          }}
        />
      )}

      {size.w > 0 && seats.map(({ seat, player }, index) => {
        const position = getRectPosition(index, seats.length, rectW, rectH);
        return (
          <Box
            key={seat}
            sx={{ position: 'absolute', left: padX + position.x - cardSize / 2, top: padY + position.y - 48, transition: 'left 0.35s ease, top 0.35s ease' }}
          >
            <PublicSeat
              seat={seat}
              player={player}
              isCurrentSpeaker={currentSpeaker === seat}
              voteTarget={voteTargets?.[seat]}
              cardSize={cardSize}
            />
          </Box>
        );
      })}

      {size.w > 0 && (
        <Box sx={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width: Math.min(rectW * 0.85, 680), maxHeight: rectH * 0.82, overflowY: 'auto' }}>
          {children}
        </Box>
      )}
    </Box>
  );
}
