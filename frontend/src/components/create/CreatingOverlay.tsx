import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Backdrop, Box, LinearProgress, Typography, keyframes,
} from '@mui/material';

import {
  BACKDROP, CARD_BACK, HAIRLINE, PLATE, SHADOW, TITLE_GRADIENT,
} from '../../theme/tokens';

// 阶段文案是氛围轮播，不是真实后端进度（创建接口不回报子阶段）。
const STAGE_KEYS = [
  'create.creatingStageDeal',
  'create.creatingStageWake',
  'create.creatingStageWarm',
] as const;
const STAGE_INTERVAL_MS = 3200;
const ELAPSED_TICK_MS = 1000;

const cardFloat = keyframes`
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-7px); }
`;
const stageFade = keyframes`
  from { opacity: 0; transform: translateY(5px); }
  to { opacity: 1; transform: translateY(0); }
`;

// 旋转放在外层、浮动放在 img 上：同一条 transform 上叠加会让旋转被动画帧覆盖。
const CARDS = [
  { rotate: -14, x: -46, delay: 0, glow: false },
  { rotate: 0, x: 0, delay: 0.35, glow: true },
  { rotate: 14, x: 46, delay: 0.7, glow: false },
] as const;

/**
 * 创建对局期间的全屏遮罩。后端创建路径要跑完模型会话探测才返回（可能十
 * 几秒），没有这个遮罩时页面静止得像卡死。用 Backdrop 而不是 Dialog：
 * Modal 会给兄弟节点加 aria-hidden，创建 pending 时表单控件会整体从
 * 可访问树消失。
 */
export default function CreatingOverlay() {
  const { t } = useTranslation();
  const [stage, setStage] = useState(0);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const stageTimer = setInterval(
      () => setStage((index) => (index + 1) % STAGE_KEYS.length),
      STAGE_INTERVAL_MS,
    );
    const tick = setInterval(
      () => setElapsed((seconds) => seconds + 1),
      ELAPSED_TICK_MS,
    );
    return () => {
      clearInterval(stageTimer);
      clearInterval(tick);
    };
  }, []);

  return (
    <Backdrop
      open
      data-testid="creating-overlay"
      aria-label={t('create.creatingOverlayAria')}
      sx={{
        position: 'fixed',
        inset: 0,
        zIndex: (theme) => theme.zIndex.modal,
        backgroundImage: `linear-gradient(${BACKDROP.scrimTop}, ${BACKDROP.scrimBottom})`,
        backdropFilter: 'blur(8px)',
      }}
    >
      <Box
        sx={{
          width: 340,
          maxWidth: '86vw',
          px: 4,
          py: 4.5,
          borderRadius: '20px',
          border: `1px solid ${HAIRLINE.strong}`,
          bgcolor: PLATE.strong,
          backdropFilter: 'blur(14px)',
          boxShadow: `0 18px 60px ${SHADOW.pop}`,
          textAlign: 'center',
        }}
      >
        <Box aria-hidden="true" sx={{ position: 'relative', height: 148, mb: 1 }}>
          {CARDS.map((card) => (
            <Box
              key={card.rotate}
              sx={{
                position: 'absolute',
                left: '50%',
                bottom: 8,
                width: 78,
                transform: `translateX(calc(-50% + ${card.x}px)) rotate(${card.rotate}deg)`,
                transformOrigin: '50% 90%',
              }}
            >
              <Box
                component="img"
                src={CARD_BACK}
                alt=""
                sx={{
                  display: 'block',
                  width: '100%',
                  borderRadius: '6px',
                  border: `1px solid ${HAIRLINE.soft}`,
                  boxShadow: card.glow
                    ? `0 0 26px var(--wk-moon-gold-dark)`
                    : `0 6px 18px ${SHADOW.pop}`,
                  animation: `${cardFloat} 2.8s ease-in-out ${card.delay}s infinite`,
                }}
              />
            </Box>
          ))}
        </Box>

        <Typography
          sx={{
            fontWeight: 700,
            fontSize: '1.35rem',
            letterSpacing: 6,
            background: TITLE_GRADIENT,
            WebkitBackgroundClip: 'text',
            backgroundClip: 'text',
            color: 'transparent',
          }}
        >
          {t('create.creatingTitle')}
        </Typography>

        <Box role="status" sx={{ mt: 1.5, minHeight: '1.6em' }}>
          <Typography
            key={stage}
            variant="body2"
            sx={{ color: 'text.secondary', animation: `${stageFade} 0.4s ease-out` }}
          >
            {t(STAGE_KEYS[stage])}
          </Typography>
        </Box>

        <LinearProgress sx={{ mt: 2.5, mx: 'auto', width: 240 }} />

        <Typography variant="caption" sx={{ display: 'block', mt: 1.5, color: 'text.disabled' }}>
          {t('create.creatingElapsed', { seconds: elapsed })}
        </Typography>
        <Typography variant="caption" sx={{ display: 'block', mt: 0.75, color: 'text.disabled' }}>
          {t('create.creatingHint')}
        </Typography>
      </Box>
    </Backdrop>
  );
}
