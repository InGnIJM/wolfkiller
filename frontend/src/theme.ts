import { createTheme } from '@mui/material/styles';
import type { Theme } from '@mui/material/styles';
import {
  cssVars, MODE_TOKENS,
} from './theme/tokens';
import type { ThemeMode } from './theme/tokens';

// 血月剧场（Crimson Gothic）主题
// 令牌来源：src/theme/tokens.ts；风格稿：frontend/design-demos/01-crimson-gothic.html
//
// 组件里引用的 CANVAS / INK / HAIRLINE 等令牌都是 `var(--wk-*)` 引用，
// 实体色值在这里按模式经 `MuiCssBaseline` 注入 `:root`。

export function createAppTheme(mode: ThemeMode): Theme {
  const t = MODE_TOKENS[mode];
  const dark = mode === 'dark';

  return createTheme({
    palette: {
      mode,
      primary: {
        main: t.bloodMoon.crimsonDeep,
        light: t.bloodMoon.crimson,
        dark: t.bloodMoon.crimsonDark,
      },
      secondary: {
        main: t.bloodMoon.gold,
        light: t.bloodMoon.goldLight,
        dark: t.bloodMoon.goldDark,
      },
      error: { main: dark ? '#F6686C' : '#C43640' },
      warning: { main: t.roleColors.hunter.color },
      success: { main: t.roleColors.villager.color },
      info: { main: t.roleColors.seer.color },
      background: {
        default: t.canvas.bg,
        paper: t.canvas.surface,
      },
      divider: t.hairline.soft,
      text: {
        primary: t.ink.primary,
        secondary: t.ink.dim,
        disabled: t.ink.faint,
      },
      action: {
        hover: t.action.hover,
        selected: t.action.selected,
        focus: t.action.focus,
        disabled: t.action.disabled,
        disabledBackground: t.action.disabledBackground,
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
          ':root': cssVars(mode),
          body: {
            margin: 0,
            WebkitFontSmoothing: 'antialiased',
            MozOsxFontSmoothing: 'grayscale',
            backgroundImage: t.bodyGradient,
            backgroundAttachment: 'fixed',
          },
          '::selection': {
            backgroundColor: t.selection,
          },
          '*': {
            scrollbarWidth: 'thin',
            scrollbarColor: `${t.hairline.strong} transparent`,
          },
        },
      },
      MuiAppBar: {
        styleOverrides: {
          root: {
            backgroundColor: t.appBar,
            backdropFilter: 'blur(10px)',
            // AppBar 默认 color="primary"，会拿 contrastText（白）当文字色 ——
            // 浅色模式下必须显式落回墨色，否则整栏白字看不见。
            color: t.ink.primary,
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
            '&:hover': { boxShadow: `0 6px 22px ${dark ? 'rgba(166,46,75,0.35)' : 'rgba(176,40,56,0.3)'}` },
          },
          outlined: {
            borderColor: t.hairline.strong,
            '&:hover': {
              borderColor: t.bloodMoon.gold,
              backgroundColor: t.action.hover,
            },
          },
        },
      },
      MuiIconButton: {
        styleOverrides: {
          root: {
            '&:hover': {
              color: t.bloodMoon.goldLight,
              backgroundColor: t.action.hover,
            },
          },
        },
      },
      MuiCard: {
        styleOverrides: {
          root: {
            borderColor: t.hairline.soft,
            backgroundImage: 'none',
          },
        },
      },
      MuiPaper: {
        styleOverrides: {
          root: { backgroundImage: 'none' },
          outlined: { borderColor: t.hairline.soft },
        },
      },
      MuiDialog: {
        styleOverrides: {
          paper: {
            borderRadius: 16,
            padding: 0,
            border: `1px solid ${t.hairline.soft}`,
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
          outlined: { borderColor: t.hairline.strong },
        },
      },
      MuiLinearProgress: {
        styleOverrides: {
          root: {
            borderRadius: 99,
            backgroundColor: dark ? 'rgba(212,168,83,0.14)' : 'rgba(138,107,34,0.16)',
          },
          bar: { backgroundColor: t.bloodMoon.crimson },
        },
      },
      MuiTabs: {
        styleOverrides: {
          indicator: {
            backgroundColor: t.bloodMoon.gold,
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
            '&.Mui-selected': { color: t.bloodMoon.goldLight },
          },
        },
      },
    },
  });
}

const theme = createAppTheme('dark');

export default theme;
