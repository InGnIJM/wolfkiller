import { Box, Tooltip, Typography, keyframes } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { GamePhase, ModelSnapshotEntry, PublicPlayerState } from '../../store/types';
import { seatModelLookup, type SeatModelInfo } from '../../store/seatModels';
import { ROLE_COLORS, BLOOD_MOON, CANVAS, CARD_BACK, HAIRLINE } from '../../theme/tokens';
import { roleMetaFor } from '../shared/roleMeta';
import SeatHoverCard from './SeatHoverCard';

// 模板字符串里的属性必须是 CSS 写法。写成 boxShadow 时浏览器会丢掉这条声明，
// 呼吸光就不会出现，座位上只剩罗马数字变色。
const speakerPulse = keyframes`
  0%, 100% { box-shadow: 0 0 0 2px rgba(229,72,77,0.95), 0 0 16px 3px rgba(229,72,77,0.72); }
  50% { box-shadow: 0 0 0 3px rgba(244,179,182,1), 0 0 28px 8px rgba(229,72,77,0.95); }
`;

// 罗马数字编号（1~12 人局）；超出 12 人回退为普通数字
const ROMAN = ['Ⅰ', 'Ⅱ', 'Ⅲ', 'Ⅳ', 'Ⅴ', 'Ⅵ', 'Ⅶ', 'Ⅷ', 'Ⅸ', 'Ⅹ', 'Ⅺ', 'Ⅻ'];
function toRoman(seat: number): string {
  return ROMAN[(seat - 1) % ROMAN.length] ?? String(seat);
}

/** 竞选期座位归属：警上（已上警）/ 警下（未上警、握有警长投票权） */
type BadgeSide = 'on' | 'off';

const BADGE_SIDE_LABEL: Record<BadgeSide, string> = { on: '警上', off: '警下' };

interface Props {
  players: Record<number, PublicPlayerState>;
  currentSpeaker?: number | null;
  voteTargets?: Record<number, number | null>;
  modelSnapshot?: ModelSnapshotEntry[];
  /** 当前阶段；只有 sheriff_election 期间才给座位挂警上/警下标 */
  phase?: GamePhase;
  /** 警上座位号（来自 store 的 badgeCandidates） */
  badgeCandidates?: number[];
  /** 警下座位号（来自 store 的 offBadgeSeats） */
  offBadgeSeats?: number[];
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
  badgeSide,
}: {
  seat: number;
  player: PublicPlayerState;
  isCurrentSpeaker: boolean;
  voteTarget: number | null | undefined;
  cardSize: number;
  compact?: boolean;
  model?: SeatModelInfo;
  badgeSide?: BadgeSide;
}) {
  const badge = roleMetaFor(player.role);
  const status = !player.is_alive ? '出局' : player.can_vote === false ? '存活·无投票权' : '存活';
  const accent = badge?.color ?? (
    player.camp === 'werewolf' ? '#E5484D'
      : player.camp === 'good' ? '#93B58C'
        : 'divider'
  );
  // 字号随卡片尺寸缩放；竖栏很窄，长身份靠字号而不是换行
  const numeralSize = clampValue(cardSize * 0.21, 11, 13);
  const roleSize = clampValue(cardSize * 0.16, 9.5, 11);
  const metaSize = clampValue(cardSize * 0.13, 8, 9);
  const railWidth = compact ? 18 : 22;
  const onBadge = badgeSide === 'on';
  const offBadge = badgeSide === 'off';
  // 竞选期用金色边框标出警上、暗金虚线标出警下；此时发言者改用红色脉冲光环，
  // 两组信号同时可见（发言者一定是警上，红脉冲只加在光环上，不再抢边框）
  const defaultBorder = isCurrentSpeaker
    ? BLOOD_MOON.crimson
    : player.is_alive ? accent : 'rgba(212,168,83,0.18)';
  const label = [
    `${seat}号`,
    badge?.label,
    status,
    badgeSide ? BADGE_SIDE_LABEL[badgeSide] : '',
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
          canVote={player.can_vote}
          isSheriff={player.is_sheriff}
          isCurrentSpeaker={isCurrentSpeaker}
          voteTarget={voteTarget}
          badgeSide={badgeSide}
          accent={badge?.color ?? (
            player.camp === 'werewolf' ? ROLE_COLORS.werewolf.color
              : player.camp === 'good' ? ROLE_COLORS.villager.color
                : BLOOD_MOON.gold
          )}
          model={model}
          art={badge?.art ?? CARD_BACK}
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
        height: compact ? 72 : SEAT_HALF_H * 2,
        flexShrink: 0,
        overflow: 'visible',
        border: '2px solid',
        borderColor: onBadge
          ? BLOOD_MOON.gold
          : offBadge ? BLOOD_MOON.goldDark : defaultBorder,
        borderStyle: offBadge ? 'dashed' : 'solid',
        borderRadius: 2,
        bgcolor: CANVAS.surface,
        boxShadow: isCurrentSpeaker
          ? '0 0 0 2px rgba(229,72,77,0.95), 0 0 22px 6px rgba(229,72,77,0.8)'
          : onBadge
            ? `0 0 0 2px ${BLOOD_MOON.gold}55, 0 0 20px 6px ${BLOOD_MOON.gold}66, 0 10px 26px rgba(0,0,0,0.45)`
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
      <Box
        sx={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          borderRadius: '6px',
        }}
      >
        <Box sx={{ flex: 1, minHeight: 0, display: 'flex' }}>
          <Box sx={{ position: 'relative', flex: 1, minWidth: 0 }}>
            <Box
              component="img"
              alt=""
              src={badge?.art ?? CARD_BACK}
              sx={{
                position: 'absolute',
                inset: 0,
                width: '100%',
                height: '100%',
                objectFit: 'cover',
                objectPosition: 'center 14%',
              }}
            />
            {isCurrentSpeaker && (
              <Box
                aria-hidden="true"
                sx={{
                  position: 'absolute',
                  inset: 0,
                  pointerEvents: 'none',
                  background: 'linear-gradient(180deg, rgba(229,72,77,0.28), rgba(229,72,77,0.08) 42%, rgba(229,72,77,0.34))',
                  boxShadow: 'inset 0 0 16px rgba(229,72,77,0.65)',
                }}
              />
            )}
            {badgeSide && (
              <Typography
                variant="caption"
                sx={{
                  position: 'absolute',
                  top: 3,
                  left: 3,
                  zIndex: 1,
                  px: 0.55,
                  py: 0.05,
                  borderRadius: 0.75,
                  fontSize: '0.55rem',
                  fontWeight: 800,
                  lineHeight: 1.5,
                  letterSpacing: 0.5,
                  whiteSpace: 'nowrap',
                  ...(onBadge
                    ? { bgcolor: BLOOD_MOON.gold, color: CANVAS.bg }
                    : {
                        bgcolor: CANVAS.surfaceRaised,
                        color: BLOOD_MOON.goldLight,
                        border: `1px dashed ${BLOOD_MOON.goldDark}`,
                      }),
                }}
              >
                {BADGE_SIDE_LABEL[badgeSide]}
              </Typography>
            )}
          </Box>
          <Box
            sx={{
              width: railWidth,
              flexShrink: 0,
              bgcolor: CANVAS.surface,
              borderLeft: `1px solid ${HAIRLINE.strong}`,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'space-between',
              pt: player.is_alive ? 0.5 : 1.75,
              pb: 0.4,
            }}
          >
            <Typography
              variant="caption"
              sx={{
                fontWeight: 800,
                fontSize: numeralSize,
                lineHeight: 1,
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
                  writingMode: 'vertical-rl',
                  fontWeight: 700,
                  fontSize: roleSize,
                  lineHeight: 1.15,
                  letterSpacing: compact ? '0.02em' : '0.08em',
                  color: badge.color,
                  ...(player.is_alive ? {} : { textDecoration: 'line-through', textDecorationColor: 'rgba(229,72,77,0.7)' }),
                }}
              >
                {badge.label}
              </Typography>
            )}
          </Box>
        </Box>
        {voteTarget !== undefined && (
          <Typography
            variant="caption"
            sx={{
              flexShrink: 0,
              bgcolor: CANVAS.surface,
              borderTop: `1px solid ${HAIRLINE.soft}`,
              fontSize: metaSize,
              fontWeight: 700,
              lineHeight: 1.3,
              textAlign: 'center',
              color: voteTarget === null ? 'text.disabled' : 'warning.light',
            }}
          >
            {voteTarget === null ? '弃权' : `→ ${voteTarget}号`}
          </Typography>
        )}
      </Box>
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
  badgeSides,
}: {
  seats: Array<{ seat: number; player: PublicPlayerState }>;
  currentSpeaker?: number | null;
  voteTargets?: Record<number, number | null>;
  cardSize: number;
  models: Record<number, SeatModelInfo>;
  badgeSides?: Map<number, BadgeSide>;
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
          badgeSide={badgeSides?.get(seat)}
        />
      ))}
    </Box>
  );
}

export default function SeatMap({
  players, currentSpeaker, voteTargets, modelSnapshot, children,
  phase, badgeCandidates, offBadgeSeats,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const models = useMemo(() => seatModelLookup(modelSnapshot), [modelSnapshot]);
  // 只在竞选期挂标，且出局座位不挂（警上自爆、警下被夜刀后都不该继续显示归属）
  const badgeSides = useMemo(() => {
    if (phase !== 'sheriff_election') return undefined;
    const sides = new Map<number, BadgeSide>();
    for (const seat of badgeCandidates ?? []) {
      if (players[seat]?.is_alive) sides.set(seat, 'on');
    }
    for (const seat of offBadgeSeats ?? []) {
      if (players[seat]?.is_alive) sides.set(seat, 'off');
    }
    return sides;
  }, [badgeCandidates, offBadgeSeats, phase, players]);

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
                  badgeSide={badgeSides?.get(seat)}
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
            badgeSides={badgeSides}
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
            badgeSides={badgeSides}
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
