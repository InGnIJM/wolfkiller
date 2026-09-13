import { useEffect } from 'react';
import { Box, IconButton, Typography, LinearProgress, Chip } from '@mui/material';
import SkipPreviousIcon from '@mui/icons-material/SkipPrevious';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import PauseIcon from '@mui/icons-material/Pause';
import SkipNextIcon from '@mui/icons-material/SkipNext';
import LiveTvIcon from '@mui/icons-material/LiveTv';
import { useGameStore, isAtTimelineEnd } from '../../store/gameStore';

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
    phase, roundNumber, isPaused, isFollowingLive, goLive,
  } = useGameStore();

  const total = timeline.length;
  const progress = total > 0 ? ((timelineIndex + 1) / total) * 100 : 0;
  const currentPhaseLabel = PHASE_LABELS[phase] || phase;
  const isAtEnd = isAtTimelineEnd(timeline, timelineIndex);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;

      if (e.key === 'ArrowRight') {
        e.preventDefault();
        stepForward();
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        stepBack();
      } else if (e.key === ' ') {
        e.preventDefault();
        if (isPlaying) pause(); else play();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [stepForward, stepBack, isPlaying, play, pause]);

  const handleProgressClick = (e: React.MouseEvent<HTMLElement>) => {
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const x = e.clientX - rect.left;
    const pct = x / rect.width;
    let idx = Math.floor(pct * (total - 1));
    idx = Math.max(0, Math.min(idx, total - 1));
    seekTo(idx);
  };

  const handleProgressKeyDown = (e: React.KeyboardEvent<HTMLElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;

    e.preventDefault();
    e.stopPropagation();
    const offset = e.key === 'ArrowRight' ? 1 : -1;
    seekTo(Math.max(0, Math.min(timelineIndex + offset, total - 1)));
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
          flexWrap: { xs: 'wrap', sm: 'nowrap' },
          bgcolor: 'background.paper',
          borderBottom: '1px solid',
          borderColor: 'divider',
        }}
      >
        <IconButton aria-label="上一个事件" size="small" onClick={stepBack} disabled={timelineIndex <= 0}>
          <SkipPreviousIcon fontSize="small" />
        </IconButton>

        <IconButton
          aria-label={isPlaying ? '暂停' : '播放'}
          size="small"
          color={isPlaying ? 'primary' : 'default'}
          onClick={isPlaying ? pause : play}
          disabled={total === 0}
        >
          {isPlaying ? <PauseIcon fontSize="small" /> : <PlayArrowIcon fontSize="small" />}
        </IconButton>

        <IconButton aria-label="下一个事件" size="small" onClick={stepForward} disabled={isAtEnd}>
          <SkipNextIcon fontSize="small" />
        </IconButton>

        <Box sx={{ display: { xs: 'none', sm: 'flex' }, gap: 0.3, ml: 1.5 }}>
          {SPEEDS.map((s) => (
            <Chip
              key={s}
              label={`${s}x`}
              size="small"
              variant={playSpeed === s ? 'filled' : 'outlined'}
              color={playSpeed === s ? 'secondary' : 'default'}
              onClick={() => setSpeed(s)}
              sx={{
                height: 24,
                minWidth: 36,
                fontSize: '0.7rem',
                fontWeight: 600,
                cursor: 'pointer',
                fontFamily: '"Cinzel","Noto Serif SC",serif',
                '& .MuiChip-label': { px: 0.8 },
              }}
            />
          ))}
        </Box>

        {!isFollowingLive && (
          <Chip
            icon={<LiveTvIcon />}
            label="回到直播"
            size="small"
            color="primary"
            onClick={goLive}
            sx={{ minHeight: 32 }}
          />
        )}

        <Typography variant="body2" color="text.secondary" sx={{ ml: 'auto', fontSize: '0.8rem' }}>
          第{roundNumber}轮 · {currentPhaseLabel}
          {isPaused && ' (已暂停)'}
        </Typography>

        <Typography variant="caption" color="text.disabled" sx={{ ml: 1.5, minWidth: 52, textAlign: 'right' }}>
          {timelineIndex + 1}/{total}
        </Typography>
      </Box>

      <Box
        aria-label="回放进度"
        aria-valuemax={Math.max(0, total - 1)}
        aria-valuemin={0}
        aria-valuenow={Math.max(0, timelineIndex)}
        aria-valuetext={total > 0 ? `第${timelineIndex + 1}条，共${total}条事件` : '暂无回放事件'}
        role="slider"
        tabIndex={0}
        sx={{
          height: 3,
          cursor: 'pointer',
          bgcolor: 'rgba(212,168,83,0.08)',
          '&:hover': { height: 5 },
          transition: 'height 0.15s',
        }}
        onClick={handleProgressClick}
        onKeyDown={handleProgressKeyDown}
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
