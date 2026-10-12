// 舞台几何。椭圆/双列只由宽度决定：w >= ELLIPSE_MIN_W 且 12 点两侧通道
// sideClearW >= SIDE_CLEAR_MIN。高度不够时缩卡片与中央面板，不改布局。
// 中央面板固有高度估计（含事件摘要约 +40）不再参与 mode 判定，超出部分滚动。
export type StageLayoutMode = 'ellipse' | 'compact';

export const ELLIPSE_MIN_W = 720;
export const SIDE_CLEAR_MIN = 240;
export const SEAT_PANEL_GAP = 8;
export const BADGE_RESERVE = 14;
export const VOTE_LINE_H = 16;
export const PANEL_MIN_H = 80;

const ELLIPSE_CARD_W_MIN = 40;
const ELLIPSE_CARD_W_MAX = 78;
const ELLIPSE_CARD_H_MIN = 56;
const ELLIPSE_CARD_H_MAX = 88;
const COMPACT_CARD_W_MIN = 30;
const COMPACT_CARD_W_MAX = 52;
const COMPACT_CARD_H_MIN = 44;
const COMPACT_CARD_H_MAX = 72;
const COMPACT_GAP = 4;
const COMPACT_COL_PAD = 8;
const COMPACT_ROW_PAD = 8;

export interface StageLayoutInput {
  width: number;
  height: number;
  seatCount: number;
  showVoteLine?: boolean;
}

export interface StageLayout {
  mode: StageLayoutMode;
  cardSize: number;
  cardH: number;
  rx: number;
  ry: number;
  panelW: number;
  panelMaxH: number;
  compactCardSize: number;
  compactCardH: number;
  compactFits: boolean;
}

export function clampValue(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function ellipseCardHeight(width: number, height: number, showVoteLine: boolean): number {
  const vote = showVoteLine ? VOTE_LINE_H : 0;
  const padY = Math.max(32, height * 0.06);
  const rectH = Math.max(120, height - padY * 2);
  const desiredBody = clampValue(
    Math.min(width, height) * 0.12,
    ELLIPSE_CARD_H_MIN,
    ELLIPSE_CARD_H_MAX - vote,
  );
  const maxFitH = (rectH - PANEL_MIN_H - 2 * BADGE_RESERVE - 2 * SEAT_PANEL_GAP) / 2;
  const bodyH = clampValue(
    Math.min(desiredBody, maxFitH - vote),
    ELLIPSE_CARD_H_MIN,
    ELLIPSE_CARD_H_MAX - vote,
  );
  return bodyH + vote;
}

export function computeStageLayout({
  width,
  height,
  seatCount,
  showVoteLine = false,
}: StageLayoutInput): StageLayout {
  const total = Math.max(1, seatCount);
  const compactCardSize = clampValue(Math.min(Math.max(width, 1), Math.max(height, 1)) * 0.13, COMPACT_CARD_W_MIN, COMPACT_CARD_W_MAX);
  const perSide = Math.ceil(total / 2);
  const colInner = height - COMPACT_ROW_PAD - COMPACT_COL_PAD;
  const fitted = (colInner - (perSide - 1) * COMPACT_GAP) / perSide;
  const vote = showVoteLine ? VOTE_LINE_H : 0;
  const compactMin = COMPACT_CARD_H_MIN + vote;
  const compactMax = COMPACT_CARD_H_MAX + vote;
  const compactCardH = clampValue(fitted, compactMin, compactMax);
  const compactNeeded = perSide * compactMin + (perSide - 1) * COMPACT_GAP + COMPACT_COL_PAD + COMPACT_ROW_PAD;
  const compactFits = height >= compactNeeded;

  if (width <= 0 || height <= 0) {
    return {
      mode: 'compact',
      cardSize: 52,
      cardH: 72,
      rx: 24,
      ry: 24,
      panelW: 240,
      panelMaxH: 120,
      compactCardSize,
      compactCardH: compactMax,
      compactFits: false,
    };
  }

  const cardSize = clampValue(Math.min(width, height) * 0.1, ELLIPSE_CARD_W_MIN, ELLIPSE_CARD_W_MAX);
  const cardW = cardSize + 10;
  const cardH = ellipseCardHeight(width, height, showVoteLine);
  const cardHalfH = cardH / 2;
  const padX = Math.max(48, width * 0.07);
  const padY = Math.max(32, height * 0.06);
  const rectW = Math.max(120, width - padX * 2);
  const rectH = Math.max(120, height - padY * 2);
  const ry = Math.max(24, rectH / 2 - cardHalfH - BADGE_RESERVE);
  const rx = Math.max(24, rectW / 2 - cardW / 2 - 4);
  const sideFactor = total >= 3 ? Math.sin((2 * Math.PI) / total) : 0;
  const sideClearW = 2 * Math.max(0, rx * sideFactor - cardW / 2 - 10);
  const panelW = Math.min(rectW * 0.72, 620, Math.max(sideClearW, 240));
  const channelH = (ry - cardHalfH - SEAT_PANEL_GAP) * 2;
  const panelMaxH = Math.max(0, channelH);
  const ellipseFeasible = width >= ELLIPSE_MIN_W && sideClearW >= SIDE_CLEAR_MIN;

  return {
    mode: ellipseFeasible ? 'ellipse' : 'compact',
    cardSize,
    cardH,
    rx,
    ry,
    panelW,
    panelMaxH,
    compactCardSize,
    compactCardH,
    compactFits,
  };
}
