import { createTheme } from '@mui/material/styles';
import {
  BLOOD_MOON, CANVAS, HAIRLINE, INK, ROLE_COLORS,
} from './theme/tokens';

// 血月剧场（Crimson Gothic）主题
// 令牌来源：src/theme/tokens.ts；风格稿：frontend/design-demos/01-crimson-gothic.html

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: BLOOD_MOON.crimsonDeep,
      light: BLOOD_MOON.crimson,
      dark: BLOOD_MOON.crimsonDark,
    },
    secondary: {
      main: BLOOD_MOON.gold,
      light: BLOOD_MOON.goldLight,
      dark: BLOOD_MOON.goldDark,
    },
    error: { main: '#F6686C' },
    warning: { main: ROLE_COLORS.hunter.color },
    success: { main: ROLE_COLORS.villager.color },
    info: { main: ROLE_COLORS.seer.color },
    background: {
      default: CANVAS.bg,
      paper: CANVAS.surface,
    },
    divider: HAIRLINE.soft,
    text: {
      primary: INK.primary,
      secondary: INK.dim,
      disabled: INK.faint,
    },
    action: {
      hover: 'rgba(212,168,83,0.09)',
      selected: 'rgba(212,168,83,0.14)',
      focus: 'rgba(212,168,83,0.18)',
      disabled: 'rgba(242,233,220,0.12)',
      disabledBackground: 'rgba(242,233,220,0.08)',
    },
  },
  typography: {
    fontFamily: '"Cinzel", "Noto Serif SC", "Songti SC", Georgia, serif',
    h4: { fontWeight: 700, letterSpacing: '-0.5px' },
    h5: { fontWeight: 700, letterSpacing: '-0.25px' },
    h6: { fontWeight: 700, letterSpacing: '-0.15px' },
    subtitle1: { fontWeight: 500, fontSize: '1rem' },
    subtitle2: { fontWeight: 500, fontSize: '0.875rem' },
    body1: { fontSize: '0.9375rem', letterSpacing: '0.1px', lineHeight: 1.6 },
    body2: { fontSize: '0.8125rem', letterSpacing: '0.1px', lineHeight: 1.6 },
    caption: { fontSize: '0.75rem', letterSpacing: '0.2px', lineHeight: 1.5 },
    button: { textTransform: 'none', fontWeight: 600, letterSpacing: '0.4px' },
  },
  shape: { borderRadius: 12 },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          margin: 0,
          WebkitFontSmoothing: 'antialiased',
          MozOsxFontSmoothing: 'grayscale',
          backgroundImage: [
            'radial-gradient(1100px 520px at 50% -8%, rgba(194,46,66,0.16), transparent 62%)',
            'radial-gradient(900px 700px at 50% 118%, rgba(212,168,83,0.07), transparent 60%)',
            `linear-gradient(180deg, ${CANVAS.bgElevated}, ${CANVAS.bg} 42%)`,
          ].join(', '),
          backgroundAttachment: 'fixed',
        },
        '::selection': {
          backgroundColor: 'rgba(229,72,77,0.35)',
        },
        '*': {
          scrollbarWidth: 'thin',
          scrollbarColor: `${HAIRLINE.strong} transparent`,
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: 'rgba(23,18,33,0.88)',
          backdropFilter: 'blur(10px)',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 999,
          padding: '8px 22px',
          fontWeight: 600,
          fontSize: '0.875rem',
          letterSpacing: '0.4px',
        },
        sizeSmall: {
          borderRadius: 999,
          padding: '4px 14px',
          fontSize: '0.75rem',
        },
        contained: {
          boxShadow: 'none',
          '&:hover': { boxShadow: '0 6px 22px rgba(194,46,66,0.35)' },
        },
        outlined: {
          borderColor: HAIRLINE.strong,
          '&:hover': {
            borderColor: BLOOD_MOON.gold,
            backgroundColor: 'rgba(212,168,83,0.06)',
          },
        },
      },
    },
    MuiIconButton: {
      styleOverrides: {
        root: {
          '&:hover': {
            color: BLOOD_MOON.goldLight,
            backgroundColor: 'rgba(212,168,83,0.09)',
          },
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          borderColor: HAIRLINE.soft,
          backgroundImage: 'none',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: { backgroundImage: 'none' },
        outlined: { borderColor: HAIRLINE.soft },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          borderRadius: 16,
          padding: 0,
          border: `1px solid ${HAIRLINE.soft}`,
        },
      },
    },
    MuiDialogTitle: {
      styleOverrides: {
        root: {
          fontSize: '1.25rem',
          fontWeight: 700,
          padding: '24px 24px 16px',
        },
      },
    },
    MuiDialogContent: {
      styleOverrides: {
        root: { padding: '16px 24px' },
      },
    },
    MuiDialogActions: {
      styleOverrides: {
        root: { padding: '16px 24px 24px' },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': { borderRadius: 10 },
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: { fontWeight: 600, borderRadius: 999 },
        outlined: { borderColor: HAIRLINE.strong },
      },
    },
    MuiLinearProgress: {
      styleOverrides: {
        root: {
          borderRadius: 99,
          backgroundColor: 'rgba(212,168,83,0.14)',
        },
        bar: { backgroundColor: BLOOD_MOON.crimson },
      },
    },
    MuiTabs: {
      styleOverrides: {
        indicator: {
          backgroundColor: BLOOD_MOON.gold,
          height: 2,
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          fontWeight: 600,
          fontSize: '0.8125rem',
          '&.Mui-selected': { color: BLOOD_MOON.goldLight },
        },
      },
    },
  },
});

export default theme;
