import {
  BrowserRouter, Link, Navigate, Route, Routes, useNavigate, useParams,
} from 'react-router-dom';
import {
  ThemeProvider, CssBaseline, Box, Typography, AppBar, Toolbar,
  Container, Button,
} from '@mui/material';
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
            <Toolbar disableGutters sx={{ minHeight: 56 }}>
              <Typography
                component={Link}
                to="/"
                variant="h6"
                sx={{
                  flexGrow: 1,
                  fontWeight: 500,
                  fontSize: '1.125rem',
                  letterSpacing: '-0.2px',
                  color: 'text.primary',
                  textDecoration: 'none',
                }}
              >
                Wolf Killer
              </Typography>
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
