import { useEffect, useState } from 'react';
import { Box, Typography, Button, Stack, Container } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import GameCard from './GameCard';
import CreateGame from './CreateGame';
import { listGames, createGame } from '../../api/client';

interface GameInfo {
  game_id: string;
  phase: string;
  round_number: number;
  player_count: number;
  alive_count: number;
  winner: string | null;
}

interface Props {
  onJoinGame: (gameId: string) => void;
}

const PHASE_LABELS: Record<string, string> = {
  waiting: '等待中',
  role_deal: '分配角色',
  night: '黑夜',
  dawn: '天亮',
  last_words: '遗言',
  speech: '发言',
  vote_casting: '投票',
  vote_resolution: '公布结果',
  game_over: '已结束',
};

export default function GameList({ onJoinGame }: Props) {
  const [games, setGames] = useState<GameInfo[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const res = await listGames();
        if (active) setGames(res.games);
      } catch (e) {
        if (active) console.error('Failed to list games:', e);
      }
    };

    void Promise.resolve().then(refresh);
    const t = setInterval(() => void refresh(), 3000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, []);

  const handleCreate = async (config: Record<string, number>) => {
    setLoading(true);
    try {
      const res = await createGame(config);
      setShowCreate(false);
      onJoinGame(res.game_id);
    } catch (e) {
      console.error('Failed to create game:', e);
    }
    setLoading(false);
  };

  return (
    <Container maxWidth="sm" sx={{ py: 4 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 4 }}>
        <Box>
          <Typography variant="h4" sx={{ fontWeight: 400, fontSize: '2rem', letterSpacing: '-0.5px' }}>
            狼人杀
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            选择一个对局加入，或创建新游戏
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => setShowCreate(true)}
          disabled={loading}
          disableElevation
        >
          创建游戏
        </Button>
      </Box>

      {games.length === 0 && (
        <Box sx={{ textAlign: 'center', py: 8 }}>
          <Typography variant="h6" color="text.disabled" sx={{ fontWeight: 400, mb: 1 }}>
            暂无对局
          </Typography>
          <Typography variant="body2" color="text.disabled">
            点击「创建游戏」开始一局新的狼人杀
          </Typography>
        </Box>
      )}

      <Stack spacing={1.5}>
        {games.map((g) => (
          <GameCard
            key={g.game_id}
            gameId={g.game_id}
            phase={PHASE_LABELS[g.phase] || g.phase}
            roundNumber={g.round_number}
            playerCount={g.player_count}
            aliveCount={g.alive_count}
            winner={g.winner}
            onClick={() => onJoinGame(g.game_id)}
          />
        ))}
      </Stack>

      <CreateGame
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreate={handleCreate}
      />
    </Container>
  );
}
