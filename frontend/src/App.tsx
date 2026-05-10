import { useState } from 'react';
import { ThemeProvider, CssBaseline, Box, Typography, AppBar, Toolbar, Container } from '@mui/material';
import theme from './theme';
import GameList from './components/lobby/GameList';
import GameBoard from './components/game/GameBoard';

function App() {
  const [gameId, setGameId] = useState<string | null>(null);

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
                variant="h6"
                sx={{
                  flexGrow: 1,
                  fontWeight: 500,
                  fontSize: '1.125rem',
                  letterSpacing: '-0.2px',
                  color: 'text.primary',
                }}
              >
                Wolf Killer
              </Typography>
              {gameId && (
                <Typography
                  variant="body2"
                  sx={{ color: 'text.secondary', fontWeight: 400 }}
                >
                  #{gameId}
                </Typography>
              )}
            </Toolbar>
          </Container>
        </AppBar>
        <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          {gameId ? (
            <GameBoard onBack={() => setGameId(null)} gameId={gameId} />
          ) : (
            <GameList onJoinGame={setGameId} />
          )}
        </Box>
      </Box>
    </ThemeProvider>
  );
}

export default App;
