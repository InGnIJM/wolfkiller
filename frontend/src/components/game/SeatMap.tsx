import { Box, Typography } from '@mui/material';
import { useEffect, useRef, useState } from 'react';
import type { PublicPlayerState } from '../../store/types';

const ROLE_BADGES: Record<string, { label: string; color: string }> = {
  'wolf-killer-werewolf': { label: '狼人', color: 'error.main' },
  'wolf-killer-witch': { label: '女巫', color: 'secondary.main' },
  'wolf-killer-seer': { label: '预言家', color: 'info.main' },
  'wolf-killer-hunter': { label: '猎人', color: 'warning.main' },
  'wolf-killer-villager': { label: '村民', color: 'success.main' },
  'wolf-killer-guard': { label: '守卫', color: 'success.light' },
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
  return (
    <Box
      sx={{
        display: 'flex',
        minWidth: cardSize,
        flexDirection: 'column',
        alignItems: 'center',
        gap: 0.25,
        p: 0.7,
        border: '1px solid',
        borderColor: isCurrentSpeaker ? 'primary.main' : (player.camp === 'werewolf' ? 'error.main' : player.camp === 'good' ? 'success.main' : 'divider'),
        borderRadius: 2,
        bgcolor: player.is_alive ? 'background.paper' : 'action.disabledBackground',
        opacity: player.is_alive ? 1 : 0.6,
      }}
    >
      <Typography variant="caption" sx={{ fontWeight: 600, lineHeight: 1.2 }}>
        {seat}号
      </Typography>
      {badge && (
        <Typography variant="caption" color={badge.color} sx={{ fontWeight: 600, fontSize: '0.68rem' }}>
          {badge.label}
        </Typography>
      )}
      <Typography variant="caption" color={player.is_alive ? 'success.light' : 'text.disabled'}>
        {player.is_alive ? '存活' : '出局'}
      </Typography>
      {player.is_sheriff && <Typography variant="caption" color="warning.light">警长</Typography>}
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
        <Box sx={{ position: 'absolute', left: padX, top: padY, width: rectW, height: rectH, border: '1px dashed', borderColor: 'divider', borderRadius: 3, pointerEvents: 'none' }} />
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
