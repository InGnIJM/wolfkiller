import { lazy, Suspense } from 'react';
import {
  BrowserRouter, Link, Navigate, Route, Routes, useNavigate, useParams,
} from 'react-router-dom';
import {
  ThemeProvider, CssBaseline, Box, Typography, AppBar, Toolbar,
  Container, Button, CircularProgress,
} from '@mui/material';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import theme from './theme';
import GameList from './components/lobby/GameList';
import GameBoard from './components/game/GameBoard';
import ModelConfigPage from './components/models/ModelConfigPage';
import CreateGameWizard from './components/create/CreateGameWizard';

const BenchmarkListPage = lazy(() => import('./components/benchmarks/BenchmarkListPage'));
const NewBenchmarkPage = lazy(() => import('./components/benchmarks/NewBenchmarkPage'));
const BenchmarkDetailPage = lazy(() => import('./components/benchmarks/BenchmarkDetailPage'));

function RouteFallback() {
  return <Box role="status" aria-label="正在加载页面" sx={{ flex: 1, display: 'grid', placeItems: 'center' }}><CircularProgress size={28} /></Box>;
}

function GameRoute() {
  const { gameId } = useParams<{ gameId: string }>();
  const navigate = useNavigate();
  if (!gameId) return <Navigate to="/" replace />;
  return <GameBoard gameId={gameId} onBack={() => navigate('/')} />;
}

export function AppShell() {
  const navigate = useNavigate();
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      {/* 血月氛围背景层：贯穿所有路由的血月光晕 + 中央暗红主晕 + 鎏金微光 + 星尘 */}
      <Box
        aria-hidden="true"
        sx={{
          position: 'fixed',
          inset: 0,
          zIndex: 0,
          pointerEvents: 'none',
          background: [
            'radial-gradient(1300px 700px at 50% -16%, rgba(229,72,77,0.34), transparent 64%)',
            'radial-gradient(1000px 760px at 50% 52%, rgba(122,28,43,0.20), transparent 68%)',
            'radial-gradient(820px 460px at 10% 110%, rgba(212,168,83,0.16), transparent 60%)',
            'radial-gradient(820px 460px at 90% 110%, rgba(194,46,66,0.14), transparent 60%)',
            'radial-gradient(2px 2px at 18% 26%, rgba(242,233,220,0.7), transparent 100%)',
            'radial-gradient(2px 2px at 74% 18%, rgba(242,233,220,0.55), transparent 100%)',
            'radial-gradient(2px 2px at 62% 36%, rgba(212,168,83,0.6), transparent 100%)',
            'radial-gradient(2px 2px at 30% 62%, rgba(242,233,220,0.4), transparent 100%)',
            'radial-gradient(2px 2px at 84% 68%, rgba(242,233,220,0.45), transparent 100%)',
            'linear-gradient(180deg, rgba(18,14,24,0.22), rgba(11,10,15,0.48))',
          ].join(', '),
        }}
      />
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
                  bgcolor: 'rgba(194,46,66,0.18)',
                  boxShadow: '0 0 16px rgba(229,72,77,0.22)',
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
                    fontWeight: 700,
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
                    fontSize: '0.6rem',
                    fontWeight: 600,
                    letterSpacing: '5px',
                    color: 'secondary.dark',
                  }}
                >
                  血月剧场 · AI WEREWOLF
                </Typography>
              </Box>
              <Button color="inherit" size="small" onClick={() => navigate('/benchmarks')}>
                模型评测
              </Button>
              <Button color="inherit" size="small" onClick={() => navigate('/models')}>
                模型管理
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
