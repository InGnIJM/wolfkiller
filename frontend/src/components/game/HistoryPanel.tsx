import { useState } from 'react';
import { Box, Typography, IconButton, Tabs, Tab, Chip } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { useGameStore } from '../../store/gameStore';
import { TimelineEntry } from '../../store/types';

interface Props {
  onClose: () => void;
}

const CAUSE_LABELS: Record<string, string> = {
  wolf_kill: '被狼人杀害', poison: '被毒杀', exile: '被放逐',
  hunter_shot: '被猎人带走', self_explode: '自爆', love_death: '殉情',
};

export default function HistoryPanel({ onClose }: Props) {
  const [tab, setTab] = useState(0);
  const { timeline, seekTo } = useGameStore();

  const handleClick = (index: number) => {
    seekTo(index);
  };

  const allEntries = timeline
    .map((entry, index) => ({ entry, originalIndex: index }))
    .filter((e) => !(e.entry.type === 'operation' && e.entry.operation === 'speak'))
    .reverse();

  const speeches = allEntries.filter(
    (e) =>
      (e.entry.type === 'conversation' && (e.entry.scope === 'public' || e.entry.scope === 'werewolf'))
  );

  const votes = allEntries.filter(
    (e) => e.entry.type === 'operation' && (e.entry.operation === 'vote' || e.entry.operation === 'vote_result')
  );

  const deaths = allEntries.filter(
    (e) => e.entry.type === 'operation' && e.entry.operation === 'night_deaths'
  );

  const werewolf = allEntries.filter(
    (e) => e.entry.type === 'conversation' && e.entry.scope === 'werewolf'
  );

  const currentList = tab === 0 ? allEntries
    : tab === 1 ? speeches
    : tab === 2 ? votes
    : tab === 3 ? deaths
    : werewolf;

  const renderEntry = (item: { entry: TimelineEntry; originalIndex: number }) => {
    const { entry, originalIndex } = item;
    const time = entry.timestamp?.slice(11, 19) || '';

    if (entry.type === 'conversation') {
      const scopeIcon = entry.scope === 'werewolf' ? '🐺' : entry.scope === 'night_intel' ? '📋' : '💬';
      const borderColor = entry.scope === 'werewolf' ? 'error.main'
        : entry.scope === 'night_intel' ? 'secondary.main'
        : 'primary.main';
      return (
        <Box
          key={originalIndex}
          onClick={() => handleClick(originalIndex)}
          sx={{
            p: 1.2, mb: 0.6, cursor: 'pointer',
            borderRadius: 2,
            borderLeft: '3px solid',
            borderColor,
            bgcolor: 'rgba(255,255,255,0.02)',
            '&:hover': { bgcolor: 'action.hover' },
            transition: 'background-color 0.15s',
          }}
        >
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Typography variant="caption" fontWeight={500}>
              {scopeIcon} {entry.speaker_seat ? `${entry.speaker_seat}号` : '系统'}
            </Typography>
            <Typography variant="caption" color="text.disabled">{time}</Typography>
          </Box>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3, fontSize: '0.72rem' }} noWrap>
            {entry.content?.slice(0, 60)}{(entry.content?.length || 0) > 60 ? '...' : ''}
          </Typography>
        </Box>
      );
    }

    if (entry.type === 'operation') {
      const op = entry.operation || '';

      if (op === 'vote') {
        return (
          <Box
            key={originalIndex} onClick={() => handleClick(originalIndex)}
            sx={{ p: 1.2, mb: 0.6, cursor: 'pointer', borderRadius: 2, bgcolor: 'rgba(255,255,255,0.02)', '&:hover': { bgcolor: 'action.hover' }, transition: 'background-color 0.15s' }}
          >
            <Box sx={{ display: 'flex', justifyContent: 'space-between' }}>
              <Typography variant="caption" fontWeight={500} color="warning.light">
                🗳 {entry.seat}号 → {entry.data?.target ? `${entry.data.target}号` : '弃权'}
              </Typography>
              <Typography variant="caption" color="text.disabled">{time}</Typography>
            </Box>
          </Box>
        );
      }
      if (op === 'vote_result') {
        return (
          <Box
            key={originalIndex} onClick={() => handleClick(originalIndex)}
            sx={{ p: 1.2, mb: 0.6, cursor: 'pointer', borderRadius: 2, border: '1px solid', borderColor: 'warning.main', bgcolor: 'rgba(255,217,104,0.04)', '&:hover': { bgcolor: 'action.hover' }, transition: 'background-color 0.15s' }}
          >
            <Typography variant="caption" fontWeight={500} color="warning.light">
              📊 {entry.data?.exiled != null ? `${entry.data.exiled}号被放逐` : '平票'}
            </Typography>
          </Box>
        );
      }
      if (op === 'night_deaths') {
        return (
          <Box
            key={originalIndex} onClick={() => handleClick(originalIndex)}
            sx={{ p: 1.2, mb: 0.6, cursor: 'pointer', borderRadius: 2, border: '1px solid', borderColor: 'error.main', bgcolor: 'rgba(242,184,181,0.04)', '&:hover': { bgcolor: 'action.hover' }, transition: 'background-color 0.15s' }}
          >
            <Typography variant="caption" fontWeight={500} color="error.light">
              ☠ {entry.data?.deaths?.map((d: any) => `${d.player_seat || d.seat}号 ${CAUSE_LABELS[d.cause] || ''}`).join(', ') || '死讯'}
            </Typography>
          </Box>
        );
      }
      if (op === 'phase_change') {
        return (
          <Box
            key={originalIndex} onClick={() => handleClick(originalIndex)}
            sx={{ p: 1.2, mb: 0.6, cursor: 'pointer', borderRadius: 2, bgcolor: 'action.hover', '&:hover': { bgcolor: 'action.selected' }, transition: 'background-color 0.15s' }}
          >
            <Typography variant="caption" fontWeight={500} color="text.secondary">
              ⏭ {entry.phase} · 第{entry.round || 0}轮
            </Typography>
          </Box>
        );
      }
      if (op === 'game_over') {
        return (
          <Box
            key={originalIndex} onClick={() => handleClick(originalIndex)}
            sx={{ p: 1.2, mb: 0.6, cursor: 'pointer', borderRadius: 2, border: '1px solid', borderColor: 'warning.main', bgcolor: 'rgba(255,217,104,0.04)', '&:hover': { bgcolor: 'action.hover' }, transition: 'background-color 0.15s' }}
          >
            <Typography variant="caption" fontWeight={500} color="warning.light">
              🏆 游戏结束 — {entry.data?.winner || '?'}获胜
            </Typography>
          </Box>
        );
      }
    }

    return null;
  };

  return (
    <Box
      sx={{
        width: 320,
        flexShrink: 0,
        display: 'flex',
        flexDirection: 'column',
        borderLeft: '1px solid',
        borderColor: 'divider',
        bgcolor: 'background.paper',
        overflow: 'hidden',
      }}
    >
      <Box sx={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        px: 2, py: 1.2,
        borderBottom: '1px solid', borderColor: 'divider',
      }}>
        <Typography fontWeight={500} variant="body1">历史记录</Typography>
        <IconButton onClick={onClose} size="small"><CloseIcon fontSize="small" /></IconButton>
      </Box>

      <Tabs
        value={tab}
        onChange={(_, v) => setTab(v)}
        variant="scrollable"
        scrollButtons={false}
        sx={{ borderBottom: '1px solid', borderColor: 'divider', minHeight: 40, '& .MuiTab-root': { minHeight: 40, py: 0.5 } }}
      >
        <Tab label={`全部 ${allEntries.length}`} />
        <Tab label={`发言 ${speeches.length}`} />
        <Tab label={`投票 ${votes.length}`} />
        <Tab label={`死亡 ${deaths.length}`} />
        <Tab label={`🐺 ${werewolf.length}`} />
      </Tabs>

      <Box sx={{ flex: 1, overflowY: 'auto', px: 1.2, py: 1 }}>
        {currentList.map(renderEntry)}
        {currentList.length === 0 && (
          <Typography variant="body2" color="text.disabled" sx={{ textAlign: 'center', py: 4 }}>
            暂无记录
          </Typography>
        )}
      </Box>
    </Box>
  );
}
