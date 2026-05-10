import { Box } from '@mui/material';
import { useRef, useState, useEffect } from 'react';
import PlayerCard from './PlayerCard';
import { PlayerFullState } from '../../store/types';

interface Props {
  players: Record<number, PlayerFullState>;
  currentSpeaker?: number | null;
  highlightedSeats?: number[];
  wolfKillTarget?: number | null;
  voteTargets?: Record<number, number | null>;
  children?: React.ReactNode;
}

function getRectPosition(
  index: number,
  total: number,
  rectW: number,
  rectH: number,
) {
  if (total <= 1) {
    return { x: rectW / 2, y: rectH / 2 };
  }

  const perimeter = 2 * (rectW + rectH);
  const step = perimeter / total;
  const dist = index * step;

  // Top edge: left → right
  if (dist < rectW) {
    return { x: dist, y: 0 };
  }
  // Right edge: top → bottom
  let remaining = dist - rectW;
  if (remaining < rectH) {
    return { x: rectW, y: remaining };
  }
  // Bottom edge: right → left
  remaining -= rectH;
  if (remaining < rectW) {
    return { x: rectW - remaining, y: rectH };
  }
  // Left edge: bottom → top
  remaining -= rectW;
  return { x: 0, y: rectH - remaining };
}

export default function SeatMap({
  players, currentSpeaker, highlightedSeats, wolfKillTarget, voteTargets, children,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const w = entry.contentRect.width;
        const h = entry.contentRect.height;
        if (Math.abs(w - size.w) > 2 || Math.abs(h - size.h) > 2) {
          setSize({ w, h });
        }
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [size.w, size.h]);

  const seats = Object.entries(players)
    .map(([s, p]) => ({ seat: parseInt(s), player: p }))
    .sort((a, b) => a.seat - b.seat);

  const total = seats.length;
  const cardSize = size.w > 0 ? Math.max(38, Math.min(64, Math.min(size.w, size.h) * 0.08)) : 52;

  // Rectangle layout: padding from container edges
  const padX = Math.max(60, size.w * 0.1);
  const padY = Math.max(50, size.h * 0.1);
  const rectW = Math.max(100, size.w - padX * 2);
  const rectH = Math.max(100, size.h - padY * 2);

  return (
    <Box
      ref={containerRef}
      sx={{
        position: 'relative',
        width: '100%',
        height: '100%',
        minHeight: 420,
        overflow: 'hidden',
      }}
    >
      {/* Subtle rectangle outline */}
      {size.w > 0 && (
        <Box
          sx={{
            position: 'absolute',
            left: padX,
            top: padY,
            width: rectW,
            height: rectH,
            border: '1px dashed',
            borderColor: 'divider',
            borderRadius: 3,
            pointerEvents: 'none',
          }}
        />
      )}

      {/* Player cards along the rectangle perimeter */}
      {size.w > 0 && seats.map(({ seat, player }, i) => {
        const pos = getRectPosition(i, total, rectW, rectH);
        return (
          <Box
            key={seat}
            sx={{
              position: 'absolute',
              left: padX + pos.x - cardSize / 2,
              top: padY + pos.y - 48,
              transition: 'left 0.35s ease, top 0.35s ease',
            }}
          >
            <PlayerCard
              seat={seat}
              player={player}
              isCurrentSpeaker={currentSpeaker === seat}
              isHighlighted={highlightedSeats?.includes(seat)}
              hasWolfClaw={wolfKillTarget === seat}
              cardSize={cardSize}
              voteTarget={voteTargets?.[seat]}
            />
          </Box>
        );
      })}

      {/* Center content area */}
      {size.w > 0 && (
        <Box
          sx={{
            position: 'absolute',
            top: '50%',
            left: '50%',
            transform: 'translate(-50%, -50%)',
            width: Math.min(rectW * 0.85, 680),
            maxHeight: rectH * 0.82,
            overflowY: 'auto',
          }}
        >
          {children}
        </Box>
      )}
    </Box>
  );
}
