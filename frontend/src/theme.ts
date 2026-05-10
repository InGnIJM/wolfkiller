import { createTheme } from '@mui/material/styles';

const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#A8C7FA',
      light: '#D3E3FD',
      dark: '#7CACF8',
    },
    secondary: {
      main: '#C4B5FD',
      light: '#E8DEF8',
      dark: '#9A82DB',
    },
    error: { main: '#F2B8B5' },
    warning: { main: '#FFD968' },
    success: { main: '#A5D6A7' },
    info: { main: '#9DBDF9' },
    background: {
      default: '#131314',
      paper: '#1C1B1F',
    },
    divider: '#49454F',
    text: {
      primary: '#E6E1E5',
      secondary: '#CAC4D0',
      disabled: '#938F99',
    },
    action: {
      hover: 'rgba(168,199,250,0.08)',
      selected: 'rgba(168,199,250,0.12)',
      disabled: 'rgba(230,225,229,0.12)',
      disabledBackground: 'rgba(230,225,229,0.08)',
    },
  },
  typography: {
    fontFamily: '"Google Sans", "Google Sans Text", Roboto, "Noto Sans SC", Arial, sans-serif',
    h4: { fontWeight: 700, letterSpacing: '-0.5px' },
    h5: { fontWeight: 500, letterSpacing: '-0.25px' },
    h6: { fontWeight: 500, letterSpacing: '-0.15px' },
    subtitle1: { fontWeight: 500, fontSize: '1rem' },
    subtitle2: { fontWeight: 500, fontSize: '0.875rem' },
    body1: { fontSize: '0.9375rem', letterSpacing: '0.1px', lineHeight: 1.6 },
    body2: { fontSize: '0.8125rem', letterSpacing: '0.1px', lineHeight: 1.6 },
    caption: { fontSize: '0.75rem', letterSpacing: '0.2px', lineHeight: 1.5 },
    button: { textTransform: 'none', fontWeight: 500, letterSpacing: '0.1px' },
  },
  shape: { borderRadius: 16 },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          margin: 0,
          WebkitFontSmoothing: 'antialiased',
          MozOsxFontSmoothing: 'grayscale',
        },
        '*': {
          scrollbarWidth: 'thin',
          scrollbarColor: '#49454F transparent',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 20,
          padding: '8px 20px',
          fontWeight: 500,
          fontSize: '0.875rem',
          letterSpacing: '0.1px',
        },
        sizeSmall: {
          borderRadius: 16,
          padding: '4px 14px',
          fontSize: '0.75rem',
        },
        contained: {
          boxShadow: 'none',
          '&:hover': { boxShadow: '0 1px 3px rgba(0,0,0,0.3)' },
        },
        outlined: {
          borderColor: '#49454F',
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          borderRadius: 16,
          borderColor: '#49454F',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        outlined: {
          borderColor: '#49454F',
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          borderRadius: 24,
          padding: 0,
        },
      },
    },
    MuiDialogTitle: {
      styleOverrides: {
        root: {
          fontSize: '1.25rem',
          fontWeight: 500,
          padding: '24px 24px 16px',
        },
      },
    },
    MuiDialogContent: {
      styleOverrides: {
        root: {
          padding: '16px 24px',
        },
      },
    },
    MuiDialogActions: {
      styleOverrides: {
        root: {
          padding: '16px 24px 24px',
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            borderRadius: 12,
          },
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 8,
          fontWeight: 500,
        },
      },
    },
    MuiLinearProgress: {
      styleOverrides: {
        root: {
          borderRadius: 2,
          backgroundColor: 'rgba(168,199,250,0.12)',
        },
      },
    },
    MuiTab: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          fontWeight: 500,
          fontSize: '0.8125rem',
        },
      },
    },
    MuiTabs: {
      styleOverrides: {
        indicator: {
          borderRadius: '3px 3px 0 0',
        },
      },
    },
  },
});

export default theme;
