import { Box, Tooltip, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { useEffect, useMemo, useRef, useState } from 'react';
import { fetchRoleCatalog } from '../../api/client';
import type { GamePhase, ModelSnapshotEntry, PublicPlayerState, RoleCatalogItem } from '../../store/types';
import { seatModelLookup, type SeatModelInfo } from '../../store/seatModels';
import { ROLE_COLORS, BLOOD_MOON, CANVAS, HAIRLINE, ON_ACCENT, SHADOW } from '../../theme/tokens';
import { useTranslation } from 'react-i18next';
import { useLabels } from '../../i18n/useLabels';
import RolePortrait from '../shared/RolePortrait';
import { roleMetaFor } from '../shared/roleMeta';
import SeatHoverCard from './SeatHoverCard';
import { clampValue, computeStageLayout } from './seatLayout';

// 罗马数字编号（1~12 人局）；超出 12 人回退为普通数字
const ROMAN = ['Ⅰ', 'Ⅱ', 'Ⅲ', 'Ⅳ', 'Ⅴ', 'Ⅵ', 'Ⅶ', 'Ⅷ', 'Ⅸ', 'Ⅹ', 'Ⅺ', 'Ⅻ'];
function toRoman(seat: number): string {
  return ROMAN[(seat - 1) % ROMAN.length] ?? String(seat);
}

/** 竞选期座位归属：警上（已上警）/ 警下（未上警、握有警长投票权） */
type BadgeSide = 'on' | 'off';

interface Props {
  players: Record<number, PublicPlayerState>;
  currentSpeaker?: number | null;
  voteTargets?: Record<number, number | null>;
  modelSnapshot?: ModelSnapshotEntry[];
  /** 当前阶段；只有 sheriff_election 期间才给座位挂警上/警下标。观众流里没有竞选
   *  阶段事件，这个值由 store 按竞选事件窗口合成，不要改成读原始事件。 */
  phase?: GamePhase;
  /** 警上座位号（来自 store 的 badgeCandidates） */
  badgeCandidates?: number[];
  /** 警下座位号（来自 store 的 offBadgeSeats） */
  offBadgeSeats?: number[];
  children?: React.ReactNode;
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
  cardHeight,
  compact = false,
  model,
  badgeSide,
  catalog,
}: {
  seat: number;
  player: PublicPlayerState;
  isCurrentSpeaker: boolean;
  voteTarget: number | null | undefined;
  cardSize: number;
  cardHeight: number;
  compact?: boolean;
  model?: SeatModelInfo;
  badgeSide?: BadgeSide;
  catalog: ReadonlyMap<string, RoleCatalogItem>;
}) {
  const { t } = useTranslation();
  const L = useLabels();
  const badge = roleMetaFor(player.role);
  const badgeLabel = L.roleName(player.role);
  const status = !player.is_alive ? t('game.dead') : player.can_vote === false ? t('game.aliveNoVote') : t('game.alive');
  const accent = badge?.color ?? (
    player.camp === 'werewolf' ? ROLE_COLORS.werewolf.color
      : player.camp === 'good' ? ROLE_COLORS.villager.color
        : player.camp === 'third_party' ? BLOOD_MOON.gold
          : 'divider'
  );
  // 字号随卡片尺寸缩放；竖栏很窄，长身份靠字号而不是换行
  const numeralSize = clampValue(cardSize * 0.21, 11, 13);
  const roleSize = clampValue(cardSize * 0.16, 9.5, 11);
  const metaSize = clampValue(cardSize * 0.13, 8, 9);
  const railWidth = compact ? 18 : 22;
  const onBadge = badgeSide === 'on';
  const offBadge = badgeSide === 'off';
  // 竞选期用金色边框标出警上、暗金虚线标出警下；此时发言者改用红色发丝环，
  // 两组信号同时可见（发言者一定是警上，红环只加在外圈，不再抢边框）
  const defaultBorder = isCurrentSpeaker
    ? BLOOD_MOON.crimson
    : player.is_alive ? accent : 'rgba(212,168,83,0.18)';
  const label = [
    t('game.seatNumber', { seat }),
    badgeLabel,
    status,
    badgeSide ? t(badgeSide === 'on' ? 'game.onBadge' : 'game.offBadge') : '',
    player.is_sheriff ? t('game.sheriff') : '',
  ].filter(Boolean).join(' ');
  const catalogEntry = player.role ? catalog.get(player.role) : undefined;

  return (
    <Tooltip
      title={(
        <SeatHoverCard
          seat={seat}
          roman={toRoman(seat)}
          roleLabel={badgeLabel}
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
          roleId={player.role}
          description={catalogEntry?.description}
          rules={catalogEntry?.rules}
        />
      )}
      placement="top"
      enterDelay={250}
      enterNextDelay={120}
      enterTouchDelay={400}
      leaveDelay={240}
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
        height: cardHeight,
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
          ? `0 0 0 2px rgba(201,86,107,0.95), 0 10px 26px ${SHADOW.card}`
          : `0 10px 26px ${SHADOW.card}`,
        opacity: player.is_alive ? 1 : 0.5,
        filter: player.is_alive ? 'none' : 'grayscale(0.7)',
        transition: 'transform 0.2s ease, border-color 0.2s ease',
        '&:hover': { transform: 'translateY(-3px)' },
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
            color: ON_ACCENT,
            bgcolor: 'warning.main',
            borderRadius: 99,
            px: 0.9,
            py: 0.1,
            fontSize: '0.65rem',
            fontWeight: 600,
            letterSpacing: 1,
            lineHeight: 1.4,
            boxShadow: `0 2px 8px ${SHADOW.card}`,
            whiteSpace: 'nowrap',
          }}
        >
          {t('game.sheriff')}
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
            color: ON_ACCENT,
            bgcolor: 'error.main',
            boxShadow: `0 2px 8px ${SHADOW.card}`,
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
            <Box sx={{ position: 'absolute', inset: 0 }}>
              <RolePortrait roleId={player.role} width="100%" height="100%" showLabel={false} />
            </Box>
            {isCurrentSpeaker && (
              <Box
                aria-hidden="true"
                sx={{
                  position: 'absolute',
                  inset: 0,
                  pointerEvents: 'none',
                  background: 'linear-gradient(180deg, rgba(201,86,107,0.28), rgba(201,86,107,0.08) 42%, rgba(201,86,107,0.34))',
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
                  fontSize: '0.62rem',
                  fontWeight: 600,
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
                {t(badgeSide === 'on' ? 'game.onBadge' : 'game.offBadge')}
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
                fontWeight: 600,
                fontSize: numeralSize,
                lineHeight: 1,
                fontFamily: '"Cinzel","Noto Serif SC",serif',
                color: isCurrentSpeaker ? ROLE_COLORS.werewolf.color : 'text.primary',
              }}
            >
              {toRoman(seat)}
            </Typography>
            {badge && (
              <Typography
                component="span"
                sx={{
                  writingMode: 'vertical-rl',
                  fontWeight: 600,
                  fontSize: roleSize,
                  lineHeight: 1.15,
                  letterSpacing: compact ? '0.02em' : '0.08em',
                  color: badge.color,
                  ...(player.is_alive ? {} : { textDecoration: 'line-through', textDecorationColor: 'rgba(201,86,107,0.7)' }),
                }}
              >
                {badgeLabel}
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
              fontWeight: 600,
              lineHeight: 1.3,
              textAlign: 'center',
              color: voteTarget === null ? 'text.disabled' : 'warning.light',
            }}
          >
            {voteTarget === null ? t('game.abstain') : `→ ${t('game.seatNumber', { seat: voteTarget })}`}
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
  cardHeight,
  compactFits,
  models,
  badgeSides,
  catalog,
}: {
  seats: Array<{ seat: number; player: PublicPlayerState }>;
  currentSpeaker?: number | null;
  voteTargets?: Record<number, number | null>;
  cardSize: number;
  cardHeight: number;
  compactFits: boolean;
  models: Record<number, SeatModelInfo>;
  badgeSides?: Map<number, BadgeSide>;
  catalog: ReadonlyMap<string, RoleCatalogItem>;
}) {
  return (
    <Box
      sx={{
        flex: 1,
        minWidth: 0,
        minHeight: 0,
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: compactFits ? 'center' : 'flex-start',
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
          cardHeight={cardHeight}
          compact
          model={models[seat]}
          badgeSide={badgeSides?.get(seat)}
          catalog={catalog}
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
  const [catalog, setCatalog] = useState<ReadonlyMap<string, RoleCatalogItem>>(
    () => new Map(),
  );
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
    let cancelled = false;
    void fetchRoleCatalog()
      .then((roles) => {
        if (cancelled) return;
        setCatalog(new Map(roles.map((role) => [role.role_id, role])));
      })
      .catch(() => {
        // 技能区可缺：身份、存活、模型仍照常展示。
      });
    return () => { cancelled = true; };
  }, []);

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
  const showVoteLine = Boolean(voteTargets && Object.keys(voteTargets).length > 0);
  const layout = computeStageLayout({
    width: size.w,
    height: size.h,
    seatCount: total,
    showVoteLine,
  });
  const perSide = Math.ceil(Math.max(1, total) / 2);
  const leftSeats = seats.slice(0, perSide);
  const rightSeats = seats.slice(perSide);

  return (
    <Box
      ref={containerRef}
      data-layout={layout.mode}
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
      {layout.mode === 'ellipse' && size.w > 0 && (
        <>
          <Box
            aria-hidden="true"
            sx={{
              position: 'absolute',
              left: '50%',
              top: '50%',
              transform: 'translate(-50%,-50%)',
              width: layout.rx * 2,
              height: layout.ry * 2,
              border: '1px dashed',
              borderColor: 'divider',
              borderRadius: '50%',
              opacity: 0.55,
              pointerEvents: 'none',
            }}
          />
          {seats.map(({ seat, player }, index) => {
            const position = getEllipsePosition(index, total, layout.rx, layout.ry);
            return (
              <Box
                key={seat}
                sx={{
                  position: 'absolute',
                  left: size.w / 2,
                  top: size.h / 2,
                  transform: `translate(-50%, -50%) translate(${position.x}px, ${position.y}px)`,
                  transition: 'transform 0.35s ease',
                }}
              >
                <PublicSeat
                  seat={seat}
                  player={player}
                  isCurrentSpeaker={currentSpeaker === seat}
                  voteTarget={voteTargets?.[seat]}
                  cardSize={layout.cardSize}
                  cardHeight={layout.cardH}
                  model={models[seat]}
                  badgeSide={badgeSides?.get(seat)}
                  catalog={catalog}
                />
              </Box>
            );
          })}
        </>
      )}

      {layout.mode === 'compact' && size.w > 0 && (
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
            minHeight: 0,
          }}
        >
          <SeatColumn
            seats={leftSeats}
            currentSpeaker={currentSpeaker}
            voteTargets={voteTargets}
            cardSize={layout.compactCardSize}
            cardHeight={layout.compactCardH}
            compactFits={layout.compactFits}
            models={models}
            badgeSides={badgeSides}
            catalog={catalog}
          />
          <Box
            sx={{
              flex: 1.4,
              minWidth: 0,
              minHeight: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              overflowY: 'auto',
              py: 1,
              containerType: 'size',
              containerName: 'panel',
            }}
          >
            {children}
          </Box>
          <SeatColumn
            seats={rightSeats}
            currentSpeaker={currentSpeaker}
            voteTargets={voteTargets}
            cardSize={layout.compactCardSize}
            cardHeight={layout.compactCardH}
            compactFits={layout.compactFits}
            models={models}
            badgeSides={badgeSides}
            catalog={catalog}
          />
        </Box>
      )}

      {layout.mode === 'ellipse' && (
        <Box
          sx={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            transform: 'translate(-50%, -50%)',
            width: layout.panelW,
            // 面板最高不超过顶点座位让出的纵向通道，超出部分滚动而不压座
            maxHeight: layout.panelMaxH,
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
