import {
  BrowserRouter, Link, Navigate, Route, Routes, useNavigate, useParams,
} from 'react-router-dom';
import {
  ThemeProvider, CssBaseline, Box, Typography, AppBar, Toolbar,
  Container, Button,
} from '@mui/material';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import theme from './theme';
import GameList from './components/lobby/GameList';
import GameBoard from './components/game/GameBoard';
import ModelConfigPage from './components/models/ModelConfigPage';
import CreateGameWizard from './components/create/CreateGameWizard';

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
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
        <AppBar
          position="static"
          elevation={0}
          sx={{
            bgcolor: 'background.paper',
            borderBottom: '1px solid',
            borderColor: 'divider',
          }}
        >
          <Container maxWidth={false}>
            <Toolbar disableGutters sx={{ minHeight: 56, gap: 1.2 }}>
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
              <Button color="inherit" onClick={() => navigate('/models')}>
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
