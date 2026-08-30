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
  guard_protect: '守卫守护',
};

const THOUGHT_LABELS: Record<string, string> = {
  witch_reasoning: '女巫思考',
  seer_reasoning: '预言家思考',
  hunter_reasoning: '猎人思考',
  guard_reasoning: '守卫思考',
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

// —— 编年史条目：类型标签 + 时间线色调 ——
const EVENT_TAGS: Record<string, string> = {
  speech: '发言',
  vote: '投票',
  vote_result: '放逐',
  night_action: '夜间',
  narration: '旁白',
  wolf_chat_message: '狼聊',
  wolf_vote: '狼票',
  witch_thought: '思考',
  seer_thought: '思考',
  night_thought: '思考',
  death: '死亡',
  phase: '阶段',
  winner: '结局',
};

function eventTone(event: PublicReplayEvent): string {
  if (event.event_type === 'death' || event.event_type === 'wolf_chat_message') return '#F4B3B6';
  if (event.event_type === 'narration') return '#7D7468';
  if (event.event_type.includes('vote')) return '#E8C887';
  if (event.event_type.includes('thought')) return '#C4B5FD';
  if (event.event_type === 'night_action') return '#9DC8E8';
  return '#D4A853';
}

// —— 编年史日分组：夜行动 → 第N夜，其余 → 第N天 ——
const NIGHT_TYPES = new Set([
  'night_action', 'wolf_vote', 'witch_thought', 'seer_thought',
  'night_thought', 'wolf_chat_message', 'narration',
]);

function dayLabel(event: PublicReplayEvent): string {
  const round = (event.payload as { round_number?: number }).round_number;
  return `第${round ?? '?'}${NIGHT_TYPES.has(event.event_type) ? '夜' : '天'}`;
}

function buildDayGroups(events: { event: PublicReplayEvent; index: number }[]) {
  const groups: { label: string; items: typeof events }[] = [];
  for (const item of events) {
    const label = dayLabel(item.event);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(item);
    else groups.push({ label, items: [item] });
  }
  return groups;
}

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
        bgcolor: 'rgba(242,233,220,0.03)',
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
    case 'technical_abstain':
      content = (
        <Typography variant="caption" color="error.light" sx={{ fontWeight: 500 }}>
          {event.payload.voter_seat}号 系统代投弃权 · 第{event.payload.round_number}轮
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
    case 'night_thought': {
      const { action_type, seat, target_seat, reasoning } = event.payload;
      content = (
        <>
          <Typography variant="caption" color="info.light" sx={{ fontWeight: 500 }}>
            {THOUGHT_LABELS[action_type] ?? '夜间思考'} · {seat}号
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3, fontSize: '0.72rem' }} noWrap>
            {target_seat === null ? `不行动：${reasoning}` : `目标${target_seat}号：${reasoning}`}
          </Typography>
        </>
      );
      break;
    }
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

  const tag = EVENT_TAGS[event.event_type] ?? '事件';
  const tone = eventTone(event);

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
        position: 'relative',
        pl: 3,
        pr: 1.2,
        py: 0.4,
        mb: 1.2,
        cursor: 'pointer',
        borderRadius: 1,
        '&::before': {
          content: '""',
          position: 'absolute',
          left: 8,
          top: 10,
          bottom: -14,
          width: 1,
          bgcolor: 'divider',
        },
        '&:hover': { '& .wk-dot': { transform: 'scale(1.35)' } },
      }}
    >
      <Box
        className="wk-dot"
        aria-hidden="true"
        sx={{
          position: 'absolute',
          left: 3.5,
          top: 9,
          width: 10,
          height: 10,
          borderRadius: '50%',
          bgcolor: 'background.paper',
          border: '2px solid',
          borderColor: tone,
          boxShadow: `0 0 7px ${tone}`,
          zIndex: 1,
          transition: 'transform 0.15s',
        }}
      />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.8, mb: 0.3 }}>
        <Typography
          variant="caption"
          sx={{
            fontSize: '0.56rem',
            fontWeight: 800,
            letterSpacing: 2,
            lineHeight: 1.4,
            color: tone,
            border: '1px solid',
            borderColor: `${tone}66`,
            borderRadius: 0.6,
            px: 0.7,
            py: 0.1,
          }}
        >
          {tag}
        </Typography>
      </Box>
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
    event.event_type === 'wolf_chat_message' || event.event_type === 'witch_thought' || event.event_type === 'seer_thought' || event.event_type === 'night_thought'
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
            {tab === 0 ? (
              buildDayGroups(currentEvents).map((group, groupIndex) => (
                <Box key={`chronicle-${groupIndex}`} sx={{ mb: 0.5 }}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mt: 1.5, mb: 0.5 }}>
                    <Box sx={{ flex: 1, height: '1px', bgcolor: 'divider' }} />
                    <Typography variant="caption" sx={{ color: 'secondary.dark', fontWeight: 700, letterSpacing: 3 }}>
                      {group.label}
                    </Typography>
                    <Box sx={{ flex: 1, height: '1px', bgcolor: 'divider' }} />
                  </Box>
                  {group.items.map(({ event, index }) => (
                    <EventCard key={index} event={event} onClick={() => handleClick(index)} />
                  ))}
                </Box>
              ))
            ) : (
              currentEvents.map(({ event, index }) => (
                <EventCard key={index} event={event} onClick={() => handleClick(index)} />
              ))
            )}
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
