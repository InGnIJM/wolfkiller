import { lazy, Suspense, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  BrowserRouter, Link, Navigate, Route, Routes, useNavigate, useParams,
} from 'react-router-dom';
import {
  ThemeProvider, CssBaseline, Box, Typography, AppBar, Toolbar,
  Container, Button, CircularProgress, useMediaQuery,
} from '@mui/material';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import i18n from './i18n';
import { createAppTheme } from './theme';
import {
  resolveLanguage, resolveThemeMode, useUiSettingsStore,
} from './store/uiSettingsStore';
import { BLOOD_MOON } from './theme/tokens';
import PageBackground from './components/shared/PageBackground';
import GameList from './components/lobby/GameList';
import GameBoard from './components/game/GameBoard';
import ModelConfigPage from './components/models/ModelConfigPage';
import SettingsPage from './components/settings/SettingsPage';
import CreateGameWizard from './components/create/CreateGameWizard';
import RulesPage from './components/rules/RulesPage';

const BenchmarkListPage = lazy(() => import('./components/benchmarks/BenchmarkListPage'));
const NewBenchmarkPage = lazy(() => import('./components/benchmarks/NewBenchmarkPage'));
const BenchmarkDetailPage = lazy(() => import('./components/benchmarks/BenchmarkDetailPage'));

function RouteFallback() {
  const { t } = useTranslation();
  return (
    <Box
      role="status"
      aria-label={t('common.loading')}
      sx={{ flex: 1, display: 'grid', placeItems: 'center' }}
    >
      <CircularProgress size={28} />
    </Box>
  );
}

function GameRoute() {
  const { gameId } = useParams<{ gameId: string }>();
  const navigate = useNavigate();
  if (!gameId) return <Navigate to="/" replace />;
  return <GameBoard gameId={gameId} onBack={() => navigate('/')} />;
}

export function AppShell() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const language = useUiSettingsStore((s) => s.language);
  const themeMode = useUiSettingsStore((s) => s.themeMode);
  const prefersDark = useMediaQuery('(prefers-color-scheme: dark)');
  const resolvedLanguage = resolveLanguage(language);
  const resolvedMode = resolveThemeMode(themeMode, prefersDark);
  const theme = useMemo(() => createAppTheme(resolvedMode), [resolvedMode]);

  useEffect(() => {
    void i18n.changeLanguage(resolvedLanguage);
    document.documentElement.lang = resolvedLanguage;
  }, [resolvedLanguage]);

  useEffect(() => {
    document.documentElement.dataset.theme = resolvedMode;
  }, [resolvedMode]);

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <PageBackground variant="main" placement="fixed" />
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100dvh', position: 'relative', zIndex: 1 }}>
        <AppBar
          position="static"
          elevation={0}
          sx={{
            borderBottom: '1px solid',
            borderColor: 'divider',
          }}
        >
          <Container maxWidth={false}>
            <Toolbar disableGutters sx={{ minHeight: 56, gap: 1.2, flexWrap: { xs: 'wrap', sm: 'nowrap' }, py: { xs: 0.5, sm: 0 } }}>
              <Box
                aria-hidden="true"
                sx={{
                  width: 34,
                  height: 34,
                  borderRadius: '50%',
                  display: 'grid',
                  placeItems: 'center',
                  color: 'secondary.main',
                  border: '1px solid',
                  borderColor: 'divider',
                  bgcolor: `color-mix(in srgb, ${BLOOD_MOON.crimsonDeep} 18%, transparent)`,
                }}
              >
                <DarkModeIcon sx={{ fontSize: 17 }} />
              </Box>
              <Box sx={{ flexGrow: 1, minWidth: 0 }}>
                <Typography
                  component={Link}
                  to="/"
                  variant="h6"
                  sx={{
                    display: 'block',
                    lineHeight: 1.15,
                    fontWeight: 600,
                    fontSize: '1.05rem',
                    letterSpacing: '3px',
                    color: 'text.primary',
                    textDecoration: 'none',
                  }}
                >
                  Wolf Killer
                </Typography>
                <Typography
                  variant="caption"
                  sx={{
                    display: 'block',
                    lineHeight: 1,
                    mt: 0.2,
                    fontSize: '0.7rem',
                    fontWeight: 600,
                    letterSpacing: '3px',
                    color: 'secondary.dark',
                  }}
                >
                  {t('nav.subtitle')}
                </Typography>
              </Box>
              <Button color="inherit" size="small" onClick={() => navigate('/rules')}>
                {t('nav.rules')}
              </Button>
              <Button color="inherit" size="small" onClick={() => navigate('/benchmarks')}>
                {t('nav.benchmarks')}
              </Button>
              <Button color="inherit" size="small" onClick={() => navigate('/models')}>
                {t('nav.models')}
              </Button>
              <Button color="inherit" size="small" onClick={() => navigate('/settings')}>
                {t('nav.settings')}
              </Button>
            </Toolbar>
          </Container>
        </AppBar>
        <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <Routes>
            <Route
              path="/"
              element={(
                <GameList
                  onJoinGame={(id) => navigate(`/game/${id}`)}
                  onCreateClick={() => navigate('/create')}
                />
              )}
            />
            <Route path="/models" element={<ModelConfigPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/rules" element={<RulesPage />} />
            <Route path="/create" element={<CreateGameWizard />} />
            <Route path="/game/:gameId" element={<GameRoute />} />
            <Route path="/benchmarks" element={<Suspense fallback={<RouteFallback />}><BenchmarkListPage /></Suspense>} />
            <Route path="/benchmarks/new" element={<Suspense fallback={<RouteFallback />}><NewBenchmarkPage /></Suspense>} />
            <Route path="/benchmarks/:id" element={<Suspense fallback={<RouteFallback />}><BenchmarkDetailPage /></Suspense>} />
          </Routes>
        </Box>
      </Box>
    </ThemeProvider>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}
