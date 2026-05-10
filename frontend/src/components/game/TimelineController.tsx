import { Box, IconButton, Typography, LinearProgress, Chip } from '@mui/material';
import SkipPreviousIcon from '@mui/icons-material/SkipPrevious';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import PauseIcon from '@mui/icons-material/Pause';
import SkipNextIcon from '@mui/icons-material/SkipNext';
import { useGameStore } from '../../store/gameStore';

const SPEEDS = [0.5, 1, 2, 4, 8];

const PHASE_LABELS: Record<string, string> = {
  waiting: '等待开始',
  role_deal: '分配角色',
  night: '夜晚',
  dawn: '天亮',
  last_words: '遗言',
  speech: '发言',
  vote_casting: '投票',
  vote_resolution: '公布结果',
  game_over: '已结束',
};

export default function TimelineController() {
  const {
    timeline, timelineIndex, isPlaying, playSpeed,
    stepBack, play, pause, stepForward, setSpeed, seekTo,
    phase, roundNumber, isPaused,
  } = useGameStore();

  const total = timeline.length;
  const progress = total > 0 ? ((timelineIndex + 1) / total) * 100 : 0;
  const currentPhaseLabel = PHASE_LABELS[phase] || phase;
  const isAtEnd = timelineIndex >= total - 1;

  const handleProgressClick = (e: React.MouseEvent<HTMLElement>) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const x = e.clientX - rect.left;
    const pct = x / rect.width;
    let idx = Math.floor(pct * (total - 1));
    idx = Math.max(0, Math.min(idx, total - 1));
    // Skip speak operations (duplicates of conversations)
    while (idx < total - 1 && timeline[idx].type === 'operation' && timeline[idx].operation === 'speak') {
      idx++;
    }
    while (idx > 0 && timeline[idx].type === 'operation' && timeline[idx].operation === 'speak') {
      idx--;
    }
    seekTo(idx);
  };

  return (
    <Box sx={{ flexShrink: 0 }}>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.4,
          px: 1.5,
          py: 0.5,
          bgcolor: 'background.paper',
          borderBottom: '1px solid',
          borderColor: 'divider',
        }}
      >
        <IconButton size="small" onClick={stepBack} disabled={timelineIndex <= 0}>
          <SkipPreviousIcon fontSize="small" />
        </IconButton>

        <IconButton
          size="small"
          color={isPlaying ? 'primary' : 'default'}
          onClick={isPlaying ? pause : play}
          disabled={isAtEnd && !isPlaying}
        >
          {isPlaying ? <PauseIcon fontSize="small" /> : <PlayArrowIcon fontSize="small" />}
        </IconButton>

        <IconButton size="small" onClick={stepForward} disabled={isAtEnd}>
          <SkipNextIcon fontSize="small" />
        </IconButton>

        <Box sx={{ display: 'flex', gap: 0.3, ml: 1.5 }}>
          {SPEEDS.map((s) => (
            <Chip
              key={s}
              label={`${s}x`}
              size="small"
              variant={playSpeed === s ? 'filled' : 'outlined'}
              color={playSpeed === s ? 'primary' : 'default'}
              onClick={() => setSpeed(s)}
              sx={{
                height: 24,
                minWidth: 36,
                fontSize: '0.7rem',
                fontWeight: 500,
                cursor: 'pointer',
                '& .MuiChip-label': { px: 0.8 },
              }}
            />
          ))}
        </Box>

        <Typography variant="body2" color="text.secondary" sx={{ ml: 'auto', fontSize: '0.8rem' }}>
          第{roundNumber}轮 · {currentPhaseLabel}
          {isPaused && ' (已暂停)'}
        </Typography>

        <Typography variant="caption" color="text.disabled" sx={{ ml: 1.5, minWidth: 52, textAlign: 'right' }}>
          {timelineIndex + 1}/{total}
        </Typography>
      </Box>

      <Box
        sx={{
          height: 3,
          cursor: 'pointer',
          bgcolor: 'rgba(168,199,250,0.08)',
          '&:hover': { height: 5 },
          transition: 'height 0.15s',
        }}
        onClick={handleProgressClick}
      >
        <LinearProgress
          variant="determinate"
          value={progress}
          sx={{
            height: '100%',
            bgcolor: 'transparent',
            '& .MuiLinearProgress-bar': { transition: 'none' },
          }}
        />
      </Box>
    </Box>
  );
}
