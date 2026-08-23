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

// 罗马数字编号（1~12 人局）；超出 12 人回退为普通数字
const ROMAN = ['Ⅰ', 'Ⅱ', 'Ⅲ', 'Ⅳ', 'Ⅴ', 'Ⅵ', 'Ⅶ', 'Ⅷ', 'Ⅸ', 'Ⅹ', 'Ⅺ', 'Ⅻ'];
function toRoman(seat: number): string {
  return ROMAN[(seat - 1) % ROMAN.length] ?? String(seat);
}

interface Props {
  players: Record<number, PublicPlayerState>;
  currentSpeaker?: number | null;
  voteTargets?: Record<number, number | null>;
  children?: React.ReactNode;
}

// 椭圆排布：任意人数沿椭圆周长均匀分布（从顶部 12 点方向起），
// 兼容后续人数扩充；半径为矩形可用区减去座位半宽，保证不贴边。
function getEllipsePosition(
  index: number,
  total: number,
  rectW: number,
  rectH: number,
  cardSize: number,
) {
  if (total <= 0) return { x: rectW / 2, y: rectH / 2 };
  const cx = rectW / 2;
  const cy = rectH / 2;
  const rx = Math.max(24, rectW / 2 - cardSize * 0.9);
  const ry = Math.max(24, rectH / 2 - cardSize * 1.1);
  const angle = (index / total) * Math.PI * 2 - Math.PI / 2;
  return { x: cx + rx * Math.cos(angle), y: cy + ry * Math.sin(angle) };
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
  const accent = badge?.color ?? (
    player.camp === 'werewolf' ? '#E5484D'
      : player.camp === 'good' ? '#93B58C'
        : 'divider'
  );
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
        width: cardSize + 10,
        flexDirection: 'column',
        alignItems: 'center',
        gap: 0.5,
        px: 1,
        py: 1.1,
        border: '2px solid',
        borderColor: isCurrentSpeaker ? '#E5484D' : (
          player.is_alive ? accent : 'rgba(212,168,83,0.18)'
        ),
        borderRadius: 2,
        bgcolor: player.is_alive ? 'rgba(23,18,33,0.85)' : 'rgba(23,18,33,0.5)',
        backgroundImage: player.is_alive
          ? 'linear-gradient(180deg, rgba(29,23,41,0.92), rgba(18,14,24,0.92))'
          : 'none',
        boxShadow: isCurrentSpeaker
          ? undefined
          : player.is_alive
            ? `0 0 14px ${accent}44, 0 10px 26px rgba(0,0,0,0.45)`
            : '0 10px 26px rgba(0,0,0,0.45)',
        opacity: player.is_alive ? 1 : 0.5,
        filter: player.is_alive ? 'none' : 'grayscale(0.7)',
        transition: 'transform 0.2s ease, border-color 0.2s ease',
        '&:hover': { transform: 'translateY(-3px)' },
        ...(isCurrentSpeaker ? { animation: `${speakerPulse} 1.2s ease-in-out infinite` } : {}),
      }}
    >
      {player.is_sheriff && (
        <Typography
          variant="caption"
          sx={{
            position: 'absolute',
            top: -10,
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
            boxShadow: '0 2px 8px rgba(0,0,0,0.45)',
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
          fontSize: 15,
          lineHeight: 1.1,
          letterSpacing: 2,
          fontFamily: '"Cinzel","Noto Serif SC",serif',
          color: isCurrentSpeaker ? '#F4B3B6' : 'text.primary',
        }}
      >
        {toRoman(seat)}
      </Typography>
      {badge && (
        <Typography
          component="span"
          sx={{
            fontWeight: 700,
            fontSize: '0.72rem',
            lineHeight: 1.3,
            letterSpacing: 3,
            textIndent: 3,
            color: badge.color,
            ...(player.is_alive ? {} : { textDecoration: 'line-through', textDecorationColor: 'rgba(229,72,77,0.7)' }),
          }}
        >
          {badge.label}
        </Typography>
      )}
      <Typography
        variant="caption"
        sx={{
          fontSize: '0.6rem',
          letterSpacing: 1.5,
          color: player.is_alive ? '#7D7468' : 'text.disabled',
        }}
      >
        {status}
      </Typography>
      {voteTarget !== undefined && (
        <Typography
          variant="caption"
          sx={{
            fontSize: '0.6rem',
            fontWeight: 600,
            color: voteTarget === null ? 'text.disabled' : 'warning.light',
          }}
        >
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
  const cardSize = size.w > 0 ? Math.max(44, Math.min(78, Math.min(size.w, size.h) * 0.1)) : 52;
  const padX = Math.max(64, size.w * 0.08);
  const padY = Math.max(56, size.h * 0.08);
  const rectW = Math.max(120, size.w - padX * 2);
  const rectH = Math.max(120, size.h - padY * 2);

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
            width: Math.max(120, rectW - cardSize * 1.6),
            height: Math.max(120, rectH - cardSize * 2),
            border: '1px dashed',
            borderColor: 'divider',
            borderRadius: '50%',
            opacity: 0.55,
            pointerEvents: 'none',
          }}
        />
      )}

      {size.w > 0 && seats.map(({ seat, player }, index) => {
        const position = getEllipsePosition(index, seats.length, rectW, rectH, cardSize);
        return (
          <Box
            key={seat}
            sx={{
              position: 'absolute',
              left: padX + position.x - (cardSize + 10) / 2,
              top: padY + position.y - 42,
              transition: 'left 0.35s ease, top 0.35s ease',
            }}
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
        <Box sx={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width: Math.min(rectW * 0.78, 620), maxHeight: rectH * 0.78, overflowY: 'auto' }}>
          {children}
        </Box>
      )}
    </Box>
  );
}
