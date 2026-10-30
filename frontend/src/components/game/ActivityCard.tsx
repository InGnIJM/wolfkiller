import Box from '@mui/material/Box';
import Typography from '@mui/material/Typography';
import { keyframes } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { useEffect, useRef } from 'react';
import { useGameStore } from '../../store/gameStore';
import type { PublicReplayEvent } from '../../store/types';
import {
  DEATH_CAUSE_LABELS, DEGRADED_ACTION_LABEL, SHERIFF_SIDE_LABELS, THOUGHT_LABELS,
  VOTE_REWOUND_RESULT, awakenedWitchPoisonResultLabel, awakenedWitchVoteLabel,
  thoughtActorName, voteRoundWasRewound,
} from '../../labels';
import { useLabels } from '../../i18n/useLabels';
import { AVATAR_INK, AVATAR_PALETTE, BLOOD_MOON, ON_ACCENT, PLATE, ROLE_COLORS, SCRIM } from '../../theme/tokens';

const livePulse = keyframes`
  0%, 100% { opacity: 0.4; transform: scale(0.85); }
  50% { opacity: 1; transform: scale(1.15); }
`;

function campLabel(camp: string): string {
  return camp === 'werewolf' ? '狼人' : camp === 'good' ? '好人' : camp === 'third_party' ? '第三方' : camp;
}

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function formatClock(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '--:--';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

interface CardProps {
  tone: string;
  children: React.ReactNode;
}

export const ACTIVITY_CARD_MAX_HEIGHT = 'min(28dvh, 220px)';
export const ACTIVITY_BODY_MAX_HEIGHT = 'min(22dvh, 160px)';

const activityBodySx = {
  flex: 1,
  minHeight: 0,
  maxHeight: ACTIVITY_BODY_MAX_HEIGHT,
  overflowY: 'auto',
  color: 'text.primary',
  lineHeight: 2,
  fontSize: '0.875rem',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  pr: 0.5,
} as const;

// 底部活动卡框架：顶部色条 + 标题行 + 正文。整卡限高，长发言在卡内滚动，
// 避免把座位舞台压到切双列 / 裁切座位。
function ActivityFrame({ tone, children }: CardProps) {
  return (
    <Box
      data-testid="activity-frame"
      data-max-height={ACTIVITY_CARD_MAX_HEIGHT}
      sx={{
        mx: 2.5,
        mb: 2,
        borderRadius: 2.5,
        border: '1px solid',
        borderColor: 'divider',
        borderTop: '2px solid',
        borderTopColor: tone,
        bgcolor: PLATE.strong,
        px: 2.5,
        py: 1.6,
        maxHeight: ACTIVITY_CARD_MAX_HEIGHT,
        display: 'flex',
        flexDirection: 'column',
        overflow: 'hidden',
      }}
    >
      {children}
    </Box>
  );
}

// 正文出现后思考区折成一行，只留末尾这么多字
const THINKING_SUMMARY_CHARS = 60;

function StreamingSpeechBody({ text, thinking }: { text: string; thinking: string }) {
  const bodyRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = bodyRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [text, thinking]);
  const thinkingOnly = !text && Boolean(thinking);
  return (
    <>
      {text && thinking ? (
        <Typography
          data-testid="streaming-thinking-summary"
          variant="caption"
          color="text.secondary"
          noWrap
          sx={{ display: 'block', opacity: 0.8 }}
        >
          思考：…{thinking.slice(-THINKING_SUMMARY_CHARS)}
        </Typography>
      ) : null}
      <Box
        ref={bodyRef}
        data-testid="activity-body"
        data-max-height={ACTIVITY_BODY_MAX_HEIGHT}
        sx={{
          flex: 1,
          minHeight: 0,
          maxHeight: ACTIVITY_BODY_MAX_HEIGHT,
          overflowY: 'auto',
          bgcolor: SCRIM.soft,
          p: 1.5,
          borderRadius: 1,
          border: '1px solid rgba(201, 86, 107, 0.25)',
        }}
      >
        {thinkingOnly ? (
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ display: 'block', letterSpacing: 2, mb: 0.5 }}
          >
            思考中
          </Typography>
        ) : null}
        <Typography
          data-testid={thinkingOnly ? 'streaming-thinking' : 'streaming-text'}
          variant={thinkingOnly ? 'caption' : 'body2'}
          sx={{
            display: 'block',
            color: thinkingOnly ? 'text.secondary' : 'text.primary',
            lineHeight: 1.8,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {thinkingOnly ? thinking : text}
          <Box
            component="span"
            sx={{
              display: 'inline-block',
              width: '2px',
              height: '1em',
              bgcolor: 'secondary.main',
              ml: 0.5,
              verticalAlign: 'text-bottom',
              animation: 'cursor-blink 1s step-start infinite',
              '@keyframes cursor-blink': {
                '0%, 100%': { opacity: 1 },
                '50%': { opacity: 0 },
              },
            }}
          />
        </Typography>
      </Box>
    </>
  );
}

function LiveTag({ label }: { label: string }) {
  return (
    <Box
      sx={{
        ml: 'auto',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 0.8,
        color: ROLE_COLORS.werewolf.color,
        fontSize: '0.65rem',
        fontWeight: 700,
        letterSpacing: 2,
      }}
    >
      <Box
        aria-hidden="true"
        sx={{
          width: 7,
          height: 7,
          borderRadius: '50%',
          bgcolor: BLOOD_MOON.crimson,
          boxShadow: '0 0 10px rgba(201,86,107,0.9)',
          animation: `${livePulse} 1.4s ease-in-out infinite`,
        }}
      />
      {label}
    </Box>
  );
}

function SeatAvatar({ seat, size = 40 }: { seat: number; size?: number }) {
  const color = AVATAR_PALETTE[(seat - 1) % AVATAR_PALETTE.length];
  return (
    <Box
      aria-hidden="true"
      sx={{
        width: size,
        height: size,
        borderRadius: '50%',
        display: 'grid',
        placeItems: 'center',
        color: AVATAR_INK,
        fontWeight: 800,
        fontSize: size * 0.4,
        bgcolor: color,
        border: '1px solid rgba(212,168,83,0.35)',
        boxShadow: '0 0 14px rgba(201,86,107,0.22)',
        flexShrink: 0,
      }}
    >
      {seat}
    </Box>
  );
}

function SpeechView({
  payload,
  timestamp,
  durationSec,
}: {
  payload: Extract<PublicReplayEvent['payload'], { player_seat: number; text: string }>;
  timestamp: string;
  durationSec: number | null;
}) {
  const kind = payload.phase === 'last_words'
    ? '遗言'
    : payload.phase === 'sheriff_election'
      ? '竞选发言'
      : '正在发言';
  const metaKind = payload.phase === 'last_words'
    ? '遗言'
    : payload.phase === 'sheriff_election'
      ? '竞选发言'
      : '发言';
  const tag = payload.phase === 'last_words'
    ? 'LAST WORDS'
    : payload.phase === 'sheriff_election'
      ? 'CAMPAIGN'
      : 'LIVE · 第' + String(payload.round_number) + '轮';
  return (
    <ActivityFrame tone={BLOOD_MOON.crimson}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1, flexShrink: 0 }}>
        <SeatAvatar seat={payload.player_seat} />
        <Box>
          <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
            {payload.player_seat}号
          </Typography>
          <Typography variant="caption" sx={{ color: 'secondary.main', letterSpacing: 3, fontWeight: 600 }}>
            {kind}
          </Typography>
        </Box>
        <LiveTag label={tag} />
      </Box>
      <Typography
        variant="body2"
        data-testid="activity-body"
        data-max-height={ACTIVITY_BODY_MAX_HEIGHT}
        sx={activityBodySx}
      >
        {payload.text}
      </Typography>
      <MetaRow
        roundNumber={payload.round_number}
        kind={metaKind}
        timestamp={timestamp}
        durationSec={durationSec}
      />
    </ActivityFrame>
  );
}

// 卡片底部元信息行：轮次 / 时点 / 耗时（相邻事件时间差）/ tokens（待后端补字段）
function MetaRow({
  roundNumber,
  kind,
  timestamp,
  durationSec,
}: {
  roundNumber: number;
  kind: string;
  timestamp: string;
  durationSec: number | null;
}) {
  return (
    <Box
      sx={{
        mt: 1.2,
        pt: 0.8,
        borderTop: '1px dashed',
        borderColor: 'divider',
        display: 'flex',
        flexWrap: 'wrap',
        gap: 2.5,
        fontSize: '0.6rem',
        color: 'text.disabled',
        letterSpacing: 1,
        flexShrink: 0,
      }}
    >
      <span>第{roundNumber}轮 · {kind}</span>
      <span>时点 {formatClock(timestamp)}</span>
      <span>耗时 {durationSec === null ? '--:--' : formatDuration(durationSec)}</span>
      <span>tokens --</span>
    </Box>
  );
}

function ThoughtView({
  label,
  seat,
  text,
  roundNumber,
  timestamp,
  durationSec,
}: {
  label: string;
  seat: number | null;
  text: string;
  roundNumber: number;
  timestamp: string;
  durationSec: number | null;
}) {
  return (
    <ActivityFrame tone={ROLE_COLORS.witch.color}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1, flexShrink: 0 }}>
        {seat !== null ? <SeatAvatar seat={seat} /> : <Box sx={{ width: 40 }} />}
        <Box>
          <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
            {seat !== null ? `${seat}号` : ''}{label}
          </Typography>
          <Typography variant="caption" sx={{ color: ROLE_COLORS.witch.color, letterSpacing: 2, fontWeight: 600 }}>
            内心思考
          </Typography>
        </Box>
        <LiveTag label="THINKING" />
      </Box>
      <Typography
        variant="body2"
        data-testid="activity-body"
        data-max-height={ACTIVITY_BODY_MAX_HEIGHT}
        sx={{ ...activityBodySx, color: 'text.secondary' }}
      >
        {text}
      </Typography>
      <MetaRow
        roundNumber={roundNumber}
        kind={label}
        timestamp={timestamp}
        durationSec={durationSec}
      />
    </ActivityFrame>
  );
}

// 狼队频道卡：狼聊与狼票共用同一张脸，只有标题/眉标/元信息文案不同。
function ChatView({
  seat,
  text,
  roundNumber,
  timestamp,
  durationSec,
  dayPlan = '',
  label = '狼群密谋',
  tag = 'WOLF CHAT',
  metaKind = '狼聊',
  tone = ROLE_COLORS.werewolf.color,
}: {
  seat: number;
  text: string;
  roundNumber: number;
  timestamp: string;
  durationSec: number | null;
  dayPlan?: string;
  label?: string;
  tag?: string;
  metaKind?: string;
  /** 卡片主色：狼队频道是粉色，觉醒女巫的协助者密谈走女巫紫。 */
  tone?: string;
}) {
  return (
    <ActivityFrame tone={tone}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1, flexShrink: 0 }}>
        <SeatAvatar seat={seat} />
        <Box>
          <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
            {seat}号
          </Typography>
          <Typography variant="caption" sx={{ color: tone, letterSpacing: 2, fontWeight: 700 }}>
            {label}
          </Typography>
        </Box>
        <LiveTag label={tag} />
      </Box>
      <Typography
        variant="body2"
        component="div"
        data-testid="activity-body"
        data-max-height={ACTIVITY_BODY_MAX_HEIGHT}
        sx={{ ...activityBodySx, color: 'text.secondary' }}
      >
        {text}
        {dayPlan ? (
          <Typography
            variant="caption"
            component="div"
            sx={{
              display: 'block',
              mt: 1,
              color: tone,
              fontWeight: 600,
              letterSpacing: 0.5,
            }}
          >
            次日计划：{dayPlan}
          </Typography>
        ) : null}
      </Typography>
      <MetaRow
        roundNumber={roundNumber}
        kind={metaKind}
        timestamp={timestamp}
        durationSec={durationSec}
      />
    </ActivityFrame>
  );
}

function DeathView({ payload }: { payload: Extract<PublicReplayEvent['payload'], { player_seat: number; cause: string; round_number: number }> }) {
  const { timeline, timelineIndex, phase } = useGameStore.getState();
  const explodeLabel = payload.cause === 'self_explode' && (
    phase === 'sheriff_election'
    || [timeline[timelineIndex - 1], timeline[timelineIndex + 1]].some(
      (event) => event?.event_type === 'sheriff_elected' && event.payload.reason === 'explode',
    )
  )
    ? '竞选自爆'
    : (DEATH_CAUSE_LABELS[payload.cause] ?? payload.cause);
  return (
    <ActivityFrame tone={ROLE_COLORS.werewolf.color}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
        <Box
          aria-hidden="true"
          sx={{
            width: 40,
            height: 40,
            borderRadius: '50%',
            display: 'grid',
            placeItems: 'center',
            color: ON_ACCENT,
            bgcolor: BLOOD_MOON.crimson,
            boxShadow: '0 0 14px rgba(201,86,107,0.5)',
            flexShrink: 0,
          }}
        >
          <CloseIcon sx={{ fontSize: 20 }} />
        </Box>
        <Box>
          <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2, color: ROLE_COLORS.werewolf.color }}>
            {payload.player_seat}号玩家出局
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ letterSpacing: 1.5 }}>
            {explodeLabel} · 第{payload.round_number}轮
          </Typography>
        </Box>
        <LiveTag label="DEATH" />
      </Box>
    </ActivityFrame>
  );
}

function VoteResultView({ payload }: { payload: Extract<PublicReplayEvent['payload'], { exiled_seat: number | null }> }) {
  const timeline = useGameStore((s) => s.timeline);
  const rewound = voteRoundWasRewound(payload.round_number, timeline);
  const exiled = payload.exiled_seat === null
    ? (rewound ? VOTE_REWOUND_RESULT : '平票，无人被放逐')
    : `${payload.exiled_seat}号被放逐`;
  return (
    <ActivityFrame tone={BLOOD_MOON.gold}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
        <Box
          aria-hidden="true"
          sx={{
            width: 40,
            height: 40,
            borderRadius: '50%',
            display: 'grid',
            placeItems: 'center',
            color: ON_ACCENT,
            bgcolor: BLOOD_MOON.gold,
            boxShadow: '0 0 14px rgba(212,168,83,0.5)',
            flexShrink: 0,
            fontSize: 20,
            fontWeight: 800,
          }}
        >
          票
        </Box>
        <Box>
          <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2, color: BLOOD_MOON.goldLight }}>
            {exiled}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ letterSpacing: 1.5 }}>
            投票结果 · 第{payload.round_number}轮
          </Typography>
        </Box>
        <LiveTag label="VOTE RESULT" />
      </Box>
    </ActivityFrame>
  );
}

// 白天裁决卡：白痴翻牌免于放逐 / 白狼王自爆带人 / 警长交徽撕徽
function DayVerdictView({
  title,
  subtitle,
  seat,
  tone,
  tag,
  detail,
}: {
  title: string;
  subtitle: string;
  seat?: number | null;
  tone: string;
  tag: string;
  /** 补充说明（目前只有警长的交/撕徽理由），没有就不占位。 */
  detail?: string | null;
}) {
  return (
    <ActivityFrame tone={tone}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
        {seat != null ? (
          <SeatAvatar seat={seat} />
        ) : (
          <Box
            aria-hidden="true"
            sx={{
              width: 40,
              height: 40,
              borderRadius: '50%',
              display: 'grid',
              placeItems: 'center',
              color: ON_ACCENT,
              bgcolor: tone,
              flexShrink: 0,
              fontSize: 16,
              fontWeight: 800,
            }}
          >
            警
          </Box>
        )}
        <Box>
          <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2, color: tone }}>
            {title}
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ letterSpacing: 1.5 }}>
            {subtitle}
          </Typography>
          {detail ? (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: 'block', mt: 0.5, lineHeight: 1.4 }}
            >
              警长思考：{detail}
            </Typography>
          ) : null}
        </Box>
        <LiveTag label={tag} />
      </Box>
    </ActivityFrame>
  );
}

export default function ActivityCard() {
  const { timeline, timelineIndex, streamingSpeech, isFollowingLive, players } = useGameStore();
  const L = useLabels();
  const entry = timelineIndex >= 0 && timelineIndex < timeline.length
    ? timeline[timelineIndex]
    : null;

  if (!entry) return null;

  // 发言耗时：距上一条事件的时间差（秒）
  let durationSec: number | null = null;
  if (timelineIndex > 0) {
    const cur = new Date(entry.timestamp).getTime();
    const prev = new Date(timeline[timelineIndex - 1].timestamp).getTime();
    if (!Number.isNaN(cur) && !Number.isNaN(prev) && cur >= prev) {
      durationSec = Math.round((cur - prev) / 1000);
    }
  }

  switch (entry.event_type) {
    case 'speaking': {
      const isStreaming = isFollowingLive
        && streamingSpeech !== null
        && streamingSpeech.seat === entry.payload.seat
        && Boolean(streamingSpeech.text || streamingSpeech.thinking);

      if (isStreaming) {
        return (
          <ActivityFrame tone={BLOOD_MOON.crimson}>
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, width: '100%', flex: 1, minHeight: 0 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
                  <SeatAvatar seat={entry.payload.seat} />
                  <Box>
                    <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
                      {entry.payload.seat}号
                    </Typography>
                    <Typography variant="caption" sx={{ color: 'secondary.main', letterSpacing: 3, fontWeight: 600 }}>
                      正在发言
                    </Typography>
                  </Box>
                </Box>
                <LiveTag label="STREAMING" />
              </Box>
              <StreamingSpeechBody text={streamingSpeech.text} thinking={streamingSpeech.thinking} />
            </Box>
          </ActivityFrame>
        );
      }

      return (
        <ActivityFrame tone={BLOOD_MOON.crimson}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <SeatAvatar seat={entry.payload.seat} />
            <Box>
              <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
                {entry.payload.seat}号
              </Typography>
              <Typography variant="caption" sx={{ color: 'secondary.main', letterSpacing: 3, fontWeight: 600 }}>
                正在发言
              </Typography>
            </Box>
            <LiveTag label="LIVE" />
          </Box>
        </ActivityFrame>
      );
    }
    case 'speech':
      return <SpeechView payload={entry.payload} timestamp={entry.timestamp} durationSec={durationSec} />;
    case 'witch_thought':
    case 'seer_thought':
      return (
        <ThoughtView
          label={THOUGHT_LABELS[entry.event_type] ?? '思考'}
          seat={entry.payload.seat}
          text={entry.payload.text}
          roundNumber={entry.payload.round_number}
          timestamp={entry.timestamp}
          durationSec={durationSec}
        />
      );
    case 'night_thought':
      // 引擎代行的动作不是这个座位的决定：眉标换成中性说法，正文只留占位理由，
      // 否则「不行动：本轮未提交决策」会被读成玩家自己决定不动手。
      return (
        <ThoughtView
          label={entry.payload.degraded
            ? DEGRADED_ACTION_LABEL
            : `${thoughtActorName(entry.payload.action_type, {
              roleName: L.roleLabelForSeat(entry.payload.seat, players),
            })}思考`}
          seat={entry.payload.seat}
          text={
            entry.payload.degraded
              ? entry.payload.reasoning || '（无理由）'
              : entry.payload.target_seat === null
                ? `不行动：${entry.payload.reasoning || '（无理由）'}`
                : `目标 ${entry.payload.target_seat}号：${entry.payload.reasoning || '（无理由）'}`
          }
          roundNumber={entry.payload.round_number}
          timestamp={entry.timestamp}
          durationSec={durationSec}
        />
      );
    case 'wolf_chat_message':
      return (
        <ChatView
          seat={entry.payload.seat}
          text={entry.payload.text}
          dayPlan={entry.payload.day_plan ?? ''}
          roundNumber={entry.payload.round_number}
          timestamp={entry.timestamp}
          durationSec={durationSec}
        />
      );
    case 'wolf_vote':
      return (
        <ChatView
          seat={entry.payload.seat}
          text={entry.payload.degraded
            // 系统代投的狼票：正文只留占位理由，「弃票」会读成这匹狼自己弃权。
            ? entry.payload.reasoning || '（无理由）'
            : entry.payload.target_seat === null
              ? `弃票：${entry.payload.reasoning || '（无理由）'}`
              : `票 → ${entry.payload.target_seat}号：${entry.payload.reasoning || '（无理由）'}`}
          roundNumber={entry.payload.round_number}
          timestamp={entry.timestamp}
          durationSec={durationSec}
          label={entry.payload.degraded ? DEGRADED_ACTION_LABEL : '狼群投票'}
          tag="WOLF VOTE"
          metaKind="狼票"
        />
      );
    case 'death':
      return <DeathView payload={entry.payload} />;
    case 'vote_result':
      return <VoteResultView payload={entry.payload} />;
    case 'vote_rewound':
      return (
        <DayVerdictView
          seat={entry.payload.seat}
          title={`${entry.payload.seat}号发动逆转时空`}
          subtitle={`放逐投票作废，重新投票 · 第${entry.payload.round_number}轮`}
          tone={BLOOD_MOON.goldLight}
          tag="REWIND"
        />
      );
    case 'exile_cancelled':
      return (
        <DayVerdictView
          seat={entry.payload.target_seat}
          title={`${entry.payload.target_seat}号翻牌免于出局`}
          subtitle={`失去投票权，之后不再能被放逐 · 第${entry.payload.round_number}轮`}
          tone={BLOOD_MOON.goldLight}
          tag="FLIPPED"
        />
      );
    case 'knight_duel':
      return (
        <DayVerdictView
          seat={entry.payload.target_seat}
          title={`${entry.payload.seat}号骑士翻牌决斗${entry.payload.target_seat}号`}
          subtitle={`${campLabel(entry.payload.camp)} · 第${entry.payload.round_number}轮`}
          tone={BLOOD_MOON.crimson}
          tag="DUEL"
        />
      );
    case 'wolf_beauty_charm': {
      // The audience is the god view, so the charmed seat is shown. Archives
      // recorded before the field existed only carry the caster.
      const charmed = entry.payload.target_seat;
      return (
        <ActivityFrame tone={ROLE_COLORS.werewolf.color}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <SeatAvatar seat={entry.payload.seat} />
            <Box>
              <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
                {charmed == null
                  ? `${entry.payload.seat}号发动魅惑`
                  : `${entry.payload.seat}号魅惑${charmed}号`}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ letterSpacing: 1.5 }}>
                {charmed == null
                  ? `第${entry.payload.round_number}轮 · 目标未记录`
                  : `第${entry.payload.round_number}轮`}
              </Typography>
            </Box>
          </Box>
        </ActivityFrame>
      );
    }
    case 'fragrance_bond':
      return (
        <ActivityFrame tone={ROLE_COLORS.witch.color}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <SeatAvatar seat={entry.payload.seat} />
            <Box>
              <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
                {`${entry.payload.seat}号缠绕${entry.payload.target_seats.join('、')}号`}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ letterSpacing: 1.5 }}>
                {`第${entry.payload.round_number}轮 · 一方出局另一方随之出局`}
              </Typography>
            </Box>
          </Box>
        </ActivityFrame>
      );
    case 'wraith_illusion': {
      // God view: who the awakened wolf beauty bound as her stand-in. Archives
      // recorded before the field existed only carry the caster.
      const standIn = entry.payload.target_seat;
      return (
        <ActivityFrame tone={ROLE_COLORS.werewolf.color}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <SeatAvatar seat={entry.payload.seat} />
            <Box>
              <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
                {standIn == null
                  ? `${entry.payload.seat}号发动挽歌幻象`
                  : `${entry.payload.seat}号以${standIn}号为替身`}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ letterSpacing: 1.5 }}>
                {standIn == null
                  ? `第${entry.payload.round_number}轮 · 目标未记录`
                  : `第${entry.payload.round_number}轮`}
              </Typography>
            </Box>
          </Box>
        </ActivityFrame>
      );
    }
    case 'wolf_beauty_revenge':
      return (
        <DayVerdictView
          seat={entry.payload.target_seat}
          title={`${entry.payload.seat}号出局，${entry.payload.target_seat}号殉情`}
          subtitle={`${DEATH_CAUSE_LABELS[entry.payload.cause] ?? entry.payload.cause} · 第${entry.payload.round_number}轮`}
          tone={ROLE_COLORS.werewolf.color}
          tag="REVENGE"
        />
      );
    case 'self_explode':
      return (
        <DayVerdictView
          seat={entry.payload.seat}
          title={`${entry.payload.seat}号自爆，带走 ${entry.payload.target_seat}号`}
          subtitle={`本日发言与投票取消，直接入夜 · 第${entry.payload.round_number}轮`}
          tone={ROLE_COLORS.werewolf.color}
          tag="SELF EXPLODE"
        />
      );
    case 'dance_pool':
      return (
        <DayVerdictView
          seat={entry.payload.seat}
          title={`${entry.payload.seat}号起舞：${entry.payload.pool_seats.join('、')}号共舞`}
          subtitle={`人数少的一方当夜出局 · 第${entry.payload.round_number}轮`}
          tone={ROLE_COLORS.witch.color}
          tag="DANCE POOL"
        />
      );
    case 'mask_granted':
      return (
        <DayVerdictView
          seat={entry.payload.seat}
          title={`${entry.payload.seat}号给 ${entry.payload.target_seat}号戴上面具`}
          subtitle={`目标${entry.payload.in_pool ? '在' : '不在'}舞池中，本次阵营比较被翻转 · 第${entry.payload.round_number}轮`}
          tone={ROLE_COLORS.witch.color}
          tag="MASK"
        />
      );
    case 'awakened_witch_assist_pick':
      return (
        <DayVerdictView
          seat={entry.payload.target_seat}
          title={`毒药目标：${entry.payload.target_seat}号`}
          subtitle={`协助者：${entry.payload.assistant_seats.map((seat) => `${seat}号`).join('、')} · 第${entry.payload.round_number}轮`}
          tone={ROLE_COLORS.witch.color}
          tag="POISON PICK"
        />
      );
    case 'awakened_witch_assist_chat':
      return (
        <ChatView
          seat={entry.payload.seat}
          text={entry.payload.text}
          roundNumber={entry.payload.round_number}
          timestamp={entry.timestamp}
          durationSec={durationSec}
          label="协助者密谈"
          tag="ASSIST CHAT"
          metaKind="密谈"
          tone={ROLE_COLORS.witch.color}
        />
      );
    case 'awakened_witch_assist_vote':
      return (
        <ChatView
          seat={entry.payload.seat}
          text={`${awakenedWitchVoteLabel(entry.payload.vote)} · 毒药目标 ${entry.payload.target_seat}号\n理由：${entry.payload.reasoning || '（无理由）'}`}
          roundNumber={entry.payload.round_number}
          timestamp={entry.timestamp}
          durationSec={durationSec}
          label="协助者表态"
          tag="ASSIST VOTE"
          metaKind="表态"
          tone={ROLE_COLORS.witch.color}
        />
      );
    case 'awakened_witch_poison_result':
      return (
        <DayVerdictView
          seat={entry.payload.target_seat}
          title={`${entry.payload.target_seat}号毒药${awakenedWitchPoisonResultLabel(entry.payload.result)}`}
          subtitle={`协助者表决结果 · 第${entry.payload.round_number}轮`}
          tone={ROLE_COLORS.witch.color}
          tag="POISON RESULT"
        />
      );
    case 'crimson_explode':
      return (
        <DayVerdictView
          seat={entry.payload.seat}
          title={`${entry.payload.seat}号自爆，封印当晚好人技能`}
          subtitle={`被封印：${entry.payload.silenced_seats.join('、') || '无人'} · 本日发言与投票取消，直接入夜 · 第${entry.payload.round_number}轮`}
          tone={ROLE_COLORS.werewolf.color}
          tag="CRIMSON EXPLODE"
        />
      );
    case 'sheriff_elected':
      return (
        <DayVerdictView
          seat={entry.payload.seat}
          title={entry.payload.seat == null ? '警长竞选结束，警徽流失' : `${entry.payload.seat}号当选警长`}
          subtitle={`第${entry.payload.round_number}轮`}
          tone={BLOOD_MOON.goldLight}
          tag="SHERIFF"
        />
      );
    case 'sheriff_badge':
      return (
        <DayVerdictView
          seat={entry.payload.to_seat ?? entry.payload.from_seat}
          title={entry.payload.to_seat == null
            ? `${entry.payload.from_seat}号撕毁警徽`
            : `${entry.payload.from_seat}号将警徽移交给${entry.payload.to_seat}号`}
          subtitle={`第${entry.payload.round_number}轮`}
          tone={BLOOD_MOON.goldLight}
          tag="BADGE"
          detail={entry.payload.reasoning ?? null}
        />
      );
    case 'sheriff_run':
      return (
        <DayVerdictView
          seat={entry.payload.seat}
          title={entry.payload.choice === 'run' ? `${entry.payload.seat}号上警` : `${entry.payload.seat}号过`}
          subtitle={`警长竞选 · 第${entry.payload.round_number}轮`}
          tone={BLOOD_MOON.goldLight}
          tag="RUN"
        />
      );
    case 'sheriff_withdraw':
      return (
        <DayVerdictView
          seat={entry.payload.seat}
          title={entry.payload.choice === 'withdraw' ? `${entry.payload.seat}号退水` : `${entry.payload.seat}号留下`}
          subtitle={`警长竞选 · 第${entry.payload.round_number}轮`}
          tone={BLOOD_MOON.goldLight}
          tag="WITHDRAW"
        />
      );
    case 'sheriff_vote':
      return (
        <DayVerdictView
          seat={entry.payload.voter_seat}
          title={entry.payload.target_seat == null
            ? `${entry.payload.voter_seat}号弃权`
            : `${entry.payload.voter_seat}号投给 ${entry.payload.target_seat}号`}
          subtitle={`${entry.payload.kind === 'pk' ? 'PK票' : '警票'} · 第${entry.payload.round_number}轮`}
          tone={BLOOD_MOON.goldLight}
          tag={entry.payload.kind === 'pk' ? 'PK VOTE' : 'SHERIFF VOTE'}
        />
      );
    case 'sheriff_side':
      return (
        <DayVerdictView
          seat={entry.payload.seat}
          title={`${entry.payload.seat}号选择${SHERIFF_SIDE_LABELS[entry.payload.side] ?? entry.payload.side}发言`}
          subtitle={`第${entry.payload.round_number}轮`}
          tone={BLOOD_MOON.goldLight}
          tag="SIDE"
        />
      );
    case 'commentary':
      return (
        <ActivityFrame tone={ROLE_COLORS.guard.color}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 1, flexShrink: 0 }}>
            <Box>
              <Typography sx={{ fontWeight: 800, letterSpacing: 1, lineHeight: 1.2 }}>
                {entry.payload.title}
              </Typography>
              <Typography variant="caption" sx={{ color: ROLE_COLORS.guard.color, letterSpacing: 2, fontWeight: 600 }}>
                上帝视角解说
              </Typography>
            </Box>
            <LiveTag label="COMMENTARY" />
          </Box>
          <Typography
            variant="body2"
            data-testid="activity-body"
            data-max-height={ACTIVITY_BODY_MAX_HEIGHT}
            sx={{
              ...activityBodySx,
              color: entry.payload.status === 'failed' ? 'text.disabled' : 'text.primary',
            }}
          >
            {entry.payload.text}
          </Typography>
          <MetaRow
            roundNumber={entry.payload.round_number}
            kind="解说"
            timestamp={entry.timestamp}
            durationSec={durationSec}
          />
        </ActivityFrame>
      );
    default:
      return null;
  }
}
