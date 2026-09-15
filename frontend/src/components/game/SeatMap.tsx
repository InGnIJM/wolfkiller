import { Box, Tooltip, Typography, keyframes } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ModelSnapshotEntry, PublicPlayerState } from '../../store/types';
import { seatModelLookup, type SeatModelInfo } from '../../store/seatModels';
import { ROLE_COLORS, BLOOD_MOON } from '../../theme/tokens';
import SeatHoverCard from './SeatHoverCard';

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
  modelSnapshot?: ModelSnapshotEntry[];
  children?: React.ReactNode;
}

// —— 自适应几何常量 ——
// 座位卡垂直半高（预留投票行后的较大值）与顶部警长徽标的上探空间
const SEAT_HALF_H = 44;
const BADGE_RESERVE = 14;
// 顶点座位与中央面板之间的最小间距
const SEAT_PANEL_GAP = 8;
// 椭圆布局要求的最小舞台宽度（再窄改用双列布局）
const ELLIPSE_MIN_W = 720;

function clampValue(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

// 椭圆排布：任意人数沿椭圆周长均匀分布（从顶部 12 点方向起），
// 兼容后续人数扩充；返回相对舞台中心的偏移。半径由调用方计算，
// 纵向已为中央面板预留通道，保证顶点座位不会压住中央内容。
function getEllipsePosition(index: number, total: number, rx: number, ry: number) {
  const angle = (index / total) * Math.PI * 2 - Math.PI / 2;
  return { x: rx * Math.cos(angle), y: ry * Math.sin(angle) };
}

function PublicSeat({
  seat,
  player,
  isCurrentSpeaker,
  voteTarget,
  cardSize,
  compact = false,
  model,
}: {
  seat: number;
  player: PublicPlayerState;
  isCurrentSpeaker: boolean;
  voteTarget: number | null | undefined;
  cardSize: number;
  compact?: boolean;
  model?: SeatModelInfo;
}) {
  const badge = player.role ? ROLE_BADGES[player.role] : undefined;
  const status = player.is_alive ? '存活' : '出局';
  const accent = badge?.color ?? (
    player.camp === 'werewolf' ? '#E5484D'
      : player.camp === 'good' ? '#93B58C'
        : 'divider'
  );
  // 字号与字距随卡片尺寸缩放，避免小卡片文字溢出
  const numeralSize = clampValue(cardSize * 0.21, 12, 15);
  const numeralSpacing = clampValue(cardSize * 0.035, 1, 2);
  const roleSize = clampValue(cardSize * 0.16, 9.5, 11.5);
  const roleSpacing = clampValue(cardSize * 0.045, 1.5, 3);
  const metaSize = clampValue(cardSize * 0.13, 8.5, 9.6);
  const label = [
    `${seat}号`,
    badge?.label,
    status,
    player.is_sheriff ? '警长' : '',
  ].filter(Boolean).join(' ');

  return (
    <Tooltip
      title={(
        <SeatHoverCard
          seat={seat}
          roman={toRoman(seat)}
          roleLabel={badge?.label}
          camp={player.camp}
          isAlive={player.is_alive}
          isSheriff={player.is_sheriff}
          isCurrentSpeaker={isCurrentSpeaker}
          voteTarget={voteTarget}
          accent={badge?.color ?? (
            player.camp === 'werewolf' ? ROLE_COLORS.werewolf.color
              : player.camp === 'good' ? ROLE_COLORS.villager.color
                : BLOOD_MOON.gold
          )}
          model={model}
        />
      )}
      placement="top"
      enterDelay={250}
      enterNextDelay={120}
      enterTouchDelay={400}
      leaveDelay={80}
      slotProps={{
        tooltip: {
          sx: {
            p: 0,
            bgcolor: 'transparent',
            boxShadow: 'none',
            maxWidth: 'none',
          },
        },
      }}
    >
    <Box
      aria-label={label}
      sx={{
        position: 'relative',
        display: 'flex',
        width: cardSize + 10,
        flexShrink: 0,
        flexDirection: 'column',
        alignItems: 'center',
        gap: compact ? 0.25 : 0.5,
        px: compact ? 0.5 : 1,
        py: compact ? 0.6 : 1.1,
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
          fontSize: numeralSize,
          lineHeight: 1.1,
          letterSpacing: numeralSpacing,
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
            fontSize: roleSize,
            lineHeight: 1.3,
            letterSpacing: roleSpacing,
            textIndent: roleSpacing,
            whiteSpace: 'nowrap',
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
          fontSize: metaSize,
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
            fontSize: metaSize,
            fontWeight: 600,
            color: voteTarget === null ? 'text.disabled' : 'warning.light',
          }}
        >
          {voteTarget === null ? '弃权' : `→ ${voteTarget}号`}
        </Typography>
      )}
    </Box>
    </Tooltip>
  );
}

function SeatColumn({
  seats,
  currentSpeaker,
  voteTargets,
  cardSize,
  models,
}: {
  seats: Array<{ seat: number; player: PublicPlayerState }>;
  currentSpeaker?: number | null;
  voteTargets?: Record<number, number | null>;
  cardSize: number;
  models: Record<number, SeatModelInfo>;
}) {
  return (
    <Box
      sx={{
        flex: 1,
        minWidth: 0,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        alignItems: 'center',
        gap: 0.5,
        py: 0.5,
        overflowY: 'auto',
      }}
    >
      {seats.map(({ seat, player }) => (
        <PublicSeat
          key={seat}
          seat={seat}
          player={player}
          isCurrentSpeaker={currentSpeaker === seat}
          voteTarget={voteTargets?.[seat]}
          cardSize={cardSize}
          compact
          model={models[seat]}
        />
      ))}
    </Box>
  );
}

export default function SeatMap({
  players, currentSpeaker, voteTargets, modelSnapshot, children,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const models = useMemo(() => seatModelLookup(modelSnapshot), [modelSnapshot]);

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
  const total = seats.length;
  const sideCount = Math.max(1, total);

  const cardSize = size.w > 0 ? clampValue(Math.min(size.w, size.h) * 0.1, 40, 78) : 52;
  const cardW = cardSize + 10;
  const padX = Math.max(48, size.w * 0.07);
  const padY = Math.max(32, size.h * 0.06);
  const rectW = Math.max(120, size.w - padX * 2);
  const rectH = Math.max(120, size.h - padY * 2);
  // 纵向半径：只保证顶点座位（12 点 / 6 点方向）完整落在容器内；
  // 中央面板的纵向避让由布局可行性判定与面板 maxHeight 双重保证
  const ry = Math.max(24, rectH / 2 - SEAT_HALF_H - BADGE_RESERVE);
  const rx = Math.max(24, rectW / 2 - cardW / 2 - 4);
  // 非顶点座位相对 12 点方向的最小水平偏移系数 = sin(2π/n)，
  // 中央面板收窄到该通道以内，避免 3/9 点方向附近座位压住面板文字
  const sideFactor = sideCount >= 3 ? Math.sin((2 * Math.PI) / sideCount) : 0;
  const sideClearW = 2 * Math.max(0, rx * sideFactor - cardW / 2 - 10);
  const panelW = Math.min(rectW * 0.72, 620, Math.max(sideClearW, 240));
  // 中央面板固有高度的保守估计（明显偏大；实测不足时面板 maxHeight 会兜底滚动）
  const centerEstimate = clampValue(size.h * 0.24 + 55, 140, 235);

  const ellipseFeasible = size.w >= ELLIPSE_MIN_W
    && ry >= centerEstimate / 2 + SEAT_HALF_H + SEAT_PANEL_GAP + 10
    && sideClearW >= 240;
  const layoutMode = ellipseFeasible ? 'ellipse' : 'compact';

  const compactCardSize = clampValue(Math.min(size.w, size.h) * 0.13, 30, 52);
  const perSide = Math.ceil(total / 2);
  const leftSeats = seats.slice(0, perSide);
  const rightSeats = seats.slice(perSide);

  return (
    <Box
      ref={containerRef}
      sx={{
        position: 'relative',
        width: '100%',
        height: '100%',
        minHeight: 160,
        overflow: 'hidden',
        containerType: 'size',
        containerName: 'stage',
      }}
    >
      {layoutMode === 'ellipse' && size.w > 0 && (
        <>
          <Box
            aria-hidden="true"
            sx={{
              position: 'absolute',
              left: '50%',
              top: '50%',
              transform: 'translate(-50%,-50%)',
              width: rx * 2,
              height: ry * 2,
              border: '1px dashed',
              borderColor: 'divider',
              borderRadius: '50%',
              opacity: 0.55,
              pointerEvents: 'none',
            }}
          />
          {seats.map(({ seat, player }, index) => {
            const position = getEllipsePosition(index, total, rx, ry);
            return (
              <Box
                key={seat}
                sx={{
                  position: 'absolute',
                  left: size.w / 2 + position.x,
                  top: size.h / 2 + position.y,
                  transform: 'translate(-50%, -50%)',
                  transition: 'left 0.35s ease, top 0.35s ease',
                }}
              >
                <PublicSeat
                  seat={seat}
                  player={player}
                  isCurrentSpeaker={currentSpeaker === seat}
                  voteTarget={voteTargets?.[seat]}
                  cardSize={cardSize}
                  model={models[seat]}
                />
              </Box>
            );
          })}
        </>
      )}

      {layoutMode === 'compact' && size.w > 0 && (
        <Box
          sx={{
            display: 'flex',
            alignItems: 'stretch',
            gap: 1,
            width: '100%',
            height: '100%',
            px: 1,
            py: 0.5,
            minWidth: 0,
          }}
        >
          <SeatColumn
            seats={leftSeats}
            currentSpeaker={currentSpeaker}
            voteTargets={voteTargets}
            cardSize={compactCardSize}
            models={models}
          />
          <Box
            sx={{
              flex: 1.4,
              minWidth: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              overflowY: 'auto',
              py: 1,
              containerType: 'inline-size',
              containerName: 'panel',
            }}
          >
            {children}
          </Box>
          <SeatColumn
            seats={rightSeats}
            currentSpeaker={currentSpeaker}
            voteTargets={voteTargets}
            cardSize={compactCardSize}
            models={models}
          />
        </Box>
      )}

      {layoutMode === 'ellipse' && (
        <Box
          sx={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            transform: 'translate(-50%, -50%)',
            width: panelW,
            // 面板最高不超过顶点座位让出的纵向通道，超出部分滚动而不压座
            maxHeight: Math.max(120, (ry - SEAT_HALF_H - SEAT_PANEL_GAP) * 2),
            overflowY: 'auto',
            containerType: 'inline-size',
            containerName: 'panel',
          }}
        >
          {children}
        </Box>
      )}
    </Box>
  );
}
