import { useState } from 'react';
import { Box, IconButton, Tab, Tabs, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { useGameStore } from '../../store/gameStore';
import type { PublicReplayEvent } from '../../store/types';

interface Props {
  onClose: () => void;
}

const CAUSE_LABELS: Record<string, string> = {
  wolf_kill: '夜间死亡',
  poison: '毒杀',
  exile: '放逐',
  hunter_shot: '猎人带走',
};

function EventCard({
  event,
  onClick,
}: {
  event: PublicReplayEvent;
  onClick: () => void;
}) {
  let content: React.ReactNode;

  switch (event.event_type) {
    case 'speech':
      content = (
        <>
          <Typography variant="caption" sx={{ fontWeight: 500 }}>
            {event.payload.player_seat}号发言 · 第{event.payload.round_number}轮
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3, fontSize: '0.72rem' }} noWrap>
            {event.payload.text}
          </Typography>
        </>
      );
      break;
    case 'vote':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {event.payload.voter_seat}号 → {event.payload.target_seat === null ? '弃权' : `${event.payload.target_seat}号`} · 第{event.payload.round_number}轮
        </Typography>
      );
      break;
    case 'vote_result':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          投票结果 · 第{event.payload.round_number}轮 · {event.payload.exiled_seat === null ? '平票' : `${event.payload.exiled_seat}号被放逐`}
        </Typography>
      );
      break;
    case 'death':
      content = (
        <Typography variant="caption" color="error.light" sx={{ fontWeight: 500 }}>
          {event.payload.player_seat}号出局 · {CAUSE_LABELS[event.payload.cause]} · 第{event.payload.round_number}轮
        </Typography>
      );
      break;
    case 'phase':
      content = (
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 500 }}>
          阶段变更：{event.payload.phase} · 第{event.payload.round_number}轮
        </Typography>
      );
      break;
    case 'winner':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          游戏结束
        </Typography>
      );
      break;
  }

  return (
    <Box
      onClick={onClick}
      sx={{
        p: 1.2,
        mb: 0.6,
        cursor: 'pointer',
        borderRadius: 2,
        borderLeft: '3px solid',
        borderColor: event.event_type === 'death' ? 'error.main' : event.event_type.includes('vote') ? 'warning.main' : 'primary.main',
        bgcolor: 'rgba(255,255,255,0.02)',
        '&:hover': { bgcolor: 'action.hover' },
        transition: 'background-color 0.15s',
      }}
    >
      {content}
    </Box>
  );
}

export default function HistoryPanel({ onClose }: Props) {
  const [tab, setTab] = useState(0);
  const { timeline, seekTo, pause } = useGameStore();
  const indexedEvents = timeline.map((event, index) => ({ event, index })).reverse();
  const speeches = indexedEvents.filter(({ event }) => event.event_type === 'speech');
  const votes = indexedEvents.filter(({ event }) => event.event_type === 'vote' || event.event_type === 'vote_result');
  const deaths = indexedEvents.filter(({ event }) => event.event_type === 'death');
  const currentEvents = tab === 0 ? indexedEvents : tab === 1 ? speeches : tab === 2 ? votes : deaths;

  const handleClick = (index: number) => {
    seekTo(index);
    pause();
  };

  return (
    <Box sx={{ width: 320, flexShrink: 0, display: 'flex', flexDirection: 'column', borderLeft: '1px solid', borderColor: 'divider', bgcolor: 'background.paper', overflow: 'hidden' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', px: 2, py: 1.2, borderBottom: '1px solid', borderColor: 'divider' }}>
        <Typography variant="body1" sx={{ fontWeight: 500 }}>历史记录</Typography>
        <IconButton onClick={onClose} size="small"><CloseIcon fontSize="small" /></IconButton>
      </Box>

      <Tabs value={tab} onChange={(_, value) => setTab(value)} variant="scrollable" scrollButtons={false} sx={{ borderBottom: '1px solid', borderColor: 'divider', minHeight: 40, '& .MuiTab-root': { minHeight: 40, py: 0.5 } }}>
        <Tab label={`全部 ${indexedEvents.length}`} />
        <Tab label={`发言 ${speeches.length}`} />
        <Tab label={`投票 ${votes.length}`} />
        <Tab label={`死亡 ${deaths.length}`} />
      </Tabs>

      <Box sx={{ flex: 1, overflowY: 'auto', px: 1.2, py: 1 }}>
        {currentEvents.map(({ event, index }) => <EventCard key={index} event={event} onClick={() => handleClick(index)} />)}
        {currentEvents.length === 0 && (
          <Typography variant="body2" color="text.disabled" sx={{ textAlign: 'center', py: 4 }}>
            暂无记录
          </Typography>
        )}
      </Box>
    </Box>
  );
}
