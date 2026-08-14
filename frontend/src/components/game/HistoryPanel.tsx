import { useEffect, useState } from 'react';
import { Box, IconButton, Tab, Tabs, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { useGameStore } from '../../store/gameStore';
import { fetchGameMemories } from '../../api/client';
import type { PlayerMemory, PublicReplayEvent } from '../../store/types';

interface Props {
  onClose: () => void;
}

const CAUSE_LABELS: Record<string, string> = {
  wolf_kill: '夜间死亡',
  poison: '毒杀',
  exile: '放逐',
  hunter_shot: '猎人带走',
};

const NIGHT_ACTION_LABELS: Record<string, string> = {
  werewolf_kill: '狼人行动',
  witch_save: '女巫救人',
  witch_poison: '女巫毒人',
  seer_check: '预言家查验',
  hunter_shot: '猎人开枪',
};

const THOUGHT_LABELS: Record<string, string> = {
  witch_reasoning: '女巫思考',
  seer_reasoning: '预言家思考',
  hunter_reasoning: '猎人思考',
  witch_thought: '女巫思考',
  seer_thought: '预言家思考',
};

const MEMORY_ACTION_LABELS: Record<string, string> = {
  kill: '狼刀',
  check: '查验',
  save: '救人',
  poison: '毒人',
  pass: '空过',
};

const MEMORY_EVENT_LABELS: Record<string, string> = {
  death: '死亡',
};

const ROLE_LABELS: Record<string, string> = {
  'wolf-killer-werewolf': '狼人',
  'wolf-killer-villager': '村民',
  'wolf-killer-seer': '预言家',
  'wolf-killer-witch': '女巫',
  'wolf-killer-hunter': '猎人',
  'wolf-killer-guard': '守卫',
};

const KNOWLEDGE_LABELS: Record<string, string> = {
  teammates: '狼队友',
  check_results: '查验记录',
  has_antidote: '解药',
  has_poison: '毒药',
  has_gun: '枪',
  last_wolf_kill_target: '昨夜狼刀目标',
};

function formatKnowledgeValue(key: string, value: unknown): string {
  if (key === 'teammates' && Array.isArray(value)) {
    return value.length ? value.map((seat) => `${seat}号`).join('、') : '无';
  }
  if (key === 'check_results' && Array.isArray(value)) {
    return value.length
      ? value.map((item) => {
          const record = item as Record<string, unknown>;
          const target = record.target_seat ?? record.target;
          const camp = record.camp ?? record.result;
          const campLabel = camp === 'werewolf' ? '狼人' : camp === 'good' ? '好人' : String(camp);
          return `${target}号→${campLabel}`;
        }).join('、')
      : '无';
  }
  if (key === 'last_wolf_kill_target' && typeof value === 'number') {
    return `${value}号`;
  }
  if (typeof value === 'boolean') return value ? '可用' : '已用';
  return String(value);
}

function formatActionHistoryItem(item: Record<string, unknown>): string {
  const round = item.round ?? '?';
  const action = item.action;
  if (action !== null && typeof action === 'object') {
    const record = action as Record<string, unknown>;
    const actionType = record.action_type ?? '?';
    const label = MEMORY_ACTION_LABELS[String(actionType)] ?? String(actionType);
    const targetSeat = record.target_seat;
    const target = targetSeat !== null && targetSeat !== undefined ? ` → ${targetSeat}号` : '';
    return `行动 ${round}轮：${label}${target}`;
  }
  if (item.type === 'speech') {
    return `发言 ${round}轮：${item.text ?? ''}`;
  }
  if (item.type === 'vote') {
    const vote = item.vote;
    const targetSeat = vote !== null && typeof vote === 'object' ? (vote as Record<string, unknown>).target_seat : undefined;
    const target = targetSeat !== null && targetSeat !== undefined ? `${targetSeat}号` : '弃权';
    return `投票 ${round}轮：${target}`;
  }
  return `行动 ${round}轮：${item.phase ?? '?'}`;
}

function formatWitnessedEvent(item: Record<string, unknown>): string {
  const round = item.round ?? '?';
  const eventType = item.event ?? item.type ?? '?';
  const label = MEMORY_EVENT_LABELS[String(eventType)] ?? String(eventType);
  return `见证 ${round}轮：${label}`;
}

function MemoryCard({ memory }: { memory: PlayerMemory }) {
  const knowledgeEntries = Object.entries(memory.private_knowledge);
  return (
    <Box
      sx={{
        p: 1.2,
        mb: 0.6,
        borderRadius: 2,
        borderLeft: '3px solid',
        borderColor: memory.is_alive ? 'success.main' : 'text.disabled',
        bgcolor: 'rgba(255,255,255,0.02)',
      }}
    >
      <Typography variant="caption" sx={{ fontWeight: 600 }}>
        {memory.seat_number}号 · {ROLE_LABELS[memory.role] ?? memory.role}
        {' · '}
        <Typography component="span" variant="caption" color={memory.is_alive ? 'success.light' : 'text.disabled'}>
          {memory.is_alive ? '存活' : '出局'}
        </Typography>
      </Typography>
      {knowledgeEntries.length > 0 && (
        <Box sx={{ mt: 0.4 }}>
          {knowledgeEntries.map(([key, value]) => (
            <Typography key={key} variant="caption" color="info.light" sx={{ display: 'block', fontSize: '0.7rem' }}>
              {KNOWLEDGE_LABELS[key] ?? key}：{formatKnowledgeValue(key, value)}
            </Typography>
          ))}
        </Box>
      )}
      {memory.action_history.length === 0 ? (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.4, fontSize: '0.7rem' }}>
          行动记录：无
        </Typography>
      ) : (
        memory.action_history.map((item, index) => (
          <Typography
            key={`action-${index}`}
            variant="caption"
            color="text.secondary"
            sx={{ display: 'block', mt: 0.2, fontSize: '0.7rem' }}
          >
            {formatActionHistoryItem(item)}
          </Typography>
        ))
      )}
      {memory.witnessed_events.length === 0 ? (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.4, fontSize: '0.7rem' }}>
          见证记录：无
        </Typography>
      ) : (
        memory.witnessed_events.map((item, index) => (
          <Typography
            key={`witnessed-${index}`}
            variant="caption"
            color="text.secondary"
            sx={{ display: 'block', mt: 0.2, fontSize: '0.7rem' }}
          >
            {formatWitnessedEvent(item)}
          </Typography>
        ))
      )}
    </Box>
  );
}

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
            {event.payload.phase === 'last_words'
              ? `${event.payload.player_seat}号遗言`
              : `${event.payload.player_seat}号发言`} · 第{event.payload.round_number}轮
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
    case 'night_action':
      content = (
        <Typography variant="caption" color="info.light" sx={{ fontWeight: 500 }}>
          {NIGHT_ACTION_LABELS[event.payload.action_type] ?? '夜晚行动'} · 目标{event.payload.target_seat}号 · 第{event.payload.round_number}轮
        </Typography>
      );
      break;
    case 'narration':
      content = (
        <Box sx={{ textAlign: 'center' }}>
          <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
            {event.payload.title}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3, fontSize: '0.72rem' }} noWrap>
            {event.payload.text}
          </Typography>
        </Box>
      );
      break;
    case 'wolf_chat_message':
      content = (
        <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.72rem' }}>
          {event.payload.seat}号：{event.payload.text}
        </Typography>
      );
      break;
    case 'wolf_vote':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {event.payload.seat}号 → {event.payload.target_seat === null ? '弃权' : `${event.payload.target_seat}号`}（{event.payload.reasoning}）
        </Typography>
      );
      break;
    case 'witch_thought':
    case 'seer_thought':
      content = (
        <>
          <Typography variant="caption" color="info.light" sx={{ fontWeight: 500 }}>
            {THOUGHT_LABELS[event.event_type]} · {event.payload.seat}号
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3, fontSize: '0.72rem' }} noWrap>
            {event.payload.text}
          </Typography>
        </>
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
      onKeyDown={(event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        onClick();
      }}
      role="button"
      tabIndex={0}
      sx={{
        p: 1.2,
        mb: 0.6,
        cursor: 'pointer',
        borderRadius: 2,
        borderLeft: '3px solid',
        borderColor: event.event_type === 'death' || event.event_type === 'wolf_chat_message'
          ? 'error.main'
          : event.event_type === 'narration'
            ? 'text.disabled'
            : event.event_type.includes('vote')
              ? 'warning.main'
              : 'primary.main',
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
  const [memories, setMemories] = useState<PlayerMemory[] | null>(null);
  const [memoriesError, setMemoriesError] = useState<string | null>(null);
  const { timeline, seekTo, pause, gameId } = useGameStore();
  const indexedEvents = timeline.map((event, index) => ({ event, index })).reverse();
  const speeches = indexedEvents.filter(({ event }) => event.event_type === 'speech');
  const votes = indexedEvents.filter(({ event }) => event.event_type === 'vote' || event.event_type === 'vote_result');
  const deaths = indexedEvents.filter(({ event }) => event.event_type === 'death');
  const nightActions = indexedEvents.filter(({ event }) => (
    event.event_type === 'night_action' || event.event_type === 'wolf_vote' || event.event_type === 'narration'
  ));
  const thoughts = indexedEvents.filter(({ event }) => (
    event.event_type === 'wolf_chat_message' || event.event_type === 'witch_thought' || event.event_type === 'seer_thought'
  ));
  const currentEvents = tab === 0 ? indexedEvents
    : tab === 1 ? speeches
    : tab === 2 ? votes
    : tab === 3 ? deaths
    : tab === 4 ? nightActions
    : thoughts;

  useEffect(() => {
    if (tab !== 6 || gameId === null) return undefined;
    let cancelled = false;
    fetchGameMemories(gameId)
      .then((result) => {
        if (!cancelled) setMemories(result.memories);
      })
      .catch(() => {
        if (!cancelled) setMemoriesError('记忆数据加载失败');
      });
    return () => { cancelled = true; };
  }, [tab, gameId]);

  const handleTabChange = (_: React.SyntheticEvent, value: number) => {
    if (value === 6) {
      setMemories(null);
      setMemoriesError(null);
    }
    setTab(value);
  };

  const handleClick = (index: number) => {
    seekTo(index);
    pause();
  };

  return (
    <Box sx={{ width: 320, flexShrink: 0, display: 'flex', flexDirection: 'column', borderLeft: '1px solid', borderColor: 'divider', bgcolor: 'background.paper', overflow: 'hidden' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', px: 2, py: 1.2, borderBottom: '1px solid', borderColor: 'divider' }}>
        <Typography variant="body1" sx={{ fontWeight: 500 }}>历史记录</Typography>
        <IconButton aria-label="关闭历史记录" onClick={onClose} size="small"><CloseIcon fontSize="small" /></IconButton>
      </Box>

      <Tabs value={tab} onChange={handleTabChange} variant="scrollable" scrollButtons={false} sx={{ borderBottom: '1px solid', borderColor: 'divider', minHeight: 40, '& .MuiTab-root': { minHeight: 40, py: 0.5 } }}>
        <Tab label={`全部 ${indexedEvents.length}`} />
        <Tab label={`发言 ${speeches.length}`} />
        <Tab label={`投票 ${votes.length}`} />
        <Tab label={`死亡 ${deaths.length}`} />
        <Tab label={`夜晚 ${nightActions.length}`} />
        <Tab label={`思考 ${thoughts.length}`} />
        <Tab label="记忆" />
      </Tabs>

      <Box sx={{ flex: 1, overflowY: 'auto', px: 1.2, py: 1 }}>
        {tab === 6 ? (
          <>
            {memoriesError !== null && (
              <Typography variant="body2" color="error" sx={{ textAlign: 'center', py: 4 }}>
                {memoriesError}
              </Typography>
            )}
            {memories === null && memoriesError === null && (
              <Typography variant="body2" color="text.disabled" sx={{ textAlign: 'center', py: 4 }}>
                加载记忆数据中…
              </Typography>
            )}
            {memories !== null && memories.length === 0 && (
              <Typography variant="body2" color="text.disabled" sx={{ textAlign: 'center', py: 4 }}>
                暂无记忆数据
              </Typography>
            )}
            {memories !== null && memories.map((memory) => (
              <MemoryCard key={memory.seat_number} memory={memory} />
            ))}
          </>
        ) : (
          <>
            {currentEvents.map(({ event, index }) => <EventCard key={index} event={event} onClick={() => handleClick(index)} />)}
            {currentEvents.length === 0 && (
              <Typography variant="body2" color="text.disabled" sx={{ textAlign: 'center', py: 4 }}>
                暂无记录
              </Typography>
            )}
          </>
        )}
      </Box>
    </Box>
  );
}
