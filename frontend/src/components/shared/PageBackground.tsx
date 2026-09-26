import { useEffect, useRef, useState } from 'react';
import { Box } from '@mui/material';

import {
  BACKDROP, BACKGROUNDS, CANVAS,
} from '../../theme/tokens';
import type { BackgroundVariant } from '../../theme/tokens';

interface Props {
  variant: BackgroundVariant;
  placement?: 'fixed' | 'contained';
}

export default function PageBackground({ variant, placement = 'fixed' }: Props) {
  const [visible, setVisible] = useState(false);
  const [failed, setFailed] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => {
    window.clearTimeout(timer.current);
  }, []);

  const reveal = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setVisible(true), BACKDROP.fadeMs);
  };

  return (
    <Box
      aria-hidden="true"
      sx={{
        position: placement === 'fixed' ? 'fixed' : 'absolute',
        inset: 0,
        zIndex: 0,
        pointerEvents: 'none',
        overflow: 'hidden',
        bgcolor: CANVAS.bg,
      }}
    >
      {!failed && (
        <Box
          component="img"
          alt=""
          src={BACKGROUNDS[variant]}
          onLoad={reveal}
          onError={() => {
            window.clearTimeout(timer.current);
            setFailed(true);
          }}
          sx={{
            position: 'absolute',
            inset: 0,
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            filter: `brightness(${BACKDROP.dim}) saturate(${BACKDROP.saturate})`,
            opacity: visible ? 1 : 0,
            transition: `opacity ${BACKDROP.fadeMs}ms ease`,
          }}
        />
      )}
      <Box
        sx={{
          position: 'absolute',
          inset: 0,
          background: `linear-gradient(180deg, ${BACKDROP.scrimTop}, ${BACKDROP.scrimBottom})`,
        }}
      />
      <Box
        sx={{
          position: 'absolute',
          inset: 0,
          background: BACKDROP.vignette,
        }}
      />
    </Box>
  );
}
