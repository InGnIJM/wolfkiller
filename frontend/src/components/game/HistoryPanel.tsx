import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Box, IconButton, Tab, Tabs, Typography } from '@mui/material';
import { useVirtualizer } from '@tanstack/react-virtual';
import CloseIcon from '@mui/icons-material/Close';
import { useGameStore } from '../../store/gameStore';
import { fetchGameMemories } from '../../api/client';
import type { PlayerMemory, PublicReplayEvent } from '../../store/types';
import i18n from '../../i18n';
import { useTranslation } from 'react-i18next';
import { useLabels } from '../../i18n/useLabels';
import type { LabelsModule } from '../../i18n/useLabels';
import { BLOOD_MOON, INK, INK_WASH, ROLE_COLORS } from '../../theme/tokens';

interface Props {
  onClose: () => void;
}

function eventTone(event: PublicReplayEvent): string {
  if (
    event.event_type === 'death'
    || event.event_type === 'wolf_chat_message'
    || event.event_type === 'self_explode'
    || event.event_type === 'speaking'
    || event.event_type === 'knight_duel'
    || event.event_type === 'wolf_beauty_charm'
    || event.event_type === 'wraith_illusion'
    || event.event_type === 'fragrance_bond'
    || event.event_type === 'wolf_beauty_revenge'
  ) return ROLE_COLORS.werewolf.color;
  if (event.event_type === 'narration') return INK.faint;
  if (event.event_type === 'commentary') return ROLE_COLORS.guard.color;
  // 觉醒女巫的毒药协助四拍都归女巫紫；放在 includes('vote') 之前，
  // 否则 assist_vote 会被当成白天/警长票染成金色。
  if (event.event_type.startsWith('awakened_witch_')) return ROLE_COLORS.witch.color;
  if (event.event_type.includes('vote')) return BLOOD_MOON.goldLight;
  if (event.event_type.includes('thought')) return ROLE_COLORS.witch.color;
  if (
    event.event_type === 'night_action'
    || event.event_type === 'dance_pool'
    || event.event_type === 'mask_granted'
  ) return ROLE_COLORS.seer.color;
  return BLOOD_MOON.gold;
}

// —— 编年史日分组：夜行动 → 第N夜，其余 → 第N天 ——
const NIGHT_TYPES = new Set([
  'night_action', 'wolf_vote', 'witch_thought', 'seer_thought',
  'night_thought', 'wolf_chat_message', 'narration', 'wolf_beauty_charm',
  'wraith_illusion', 'dance_pool', 'mask_granted', 'fragrance_bond',
  // 觉醒女巫的调配毒药整晚都在夜间频道里发生。
  'awakened_witch_assist_pick', 'awakened_witch_assist_chat',
  'awakened_witch_assist_vote', 'awakened_witch_poison_result',
]);

// A revenge lands in the phase of the exit that triggered it: an exile or a
// duel is settled in daylight, every other cause is settled during the night.
const DAYTIME_REVENGE_CAUSES: ReadonlySet<string> = new Set(['exile', 'knight_duel']);

function isNightEvent(event: PublicReplayEvent): boolean {
  if (event.event_type === 'commentary') return event.payload.beat === 'night';
  if (NIGHT_TYPES.has(event.event_type)) return true;
  return event.event_type === 'wolf_beauty_revenge'
    && !DAYTIME_REVENGE_CAUSES.has(event.payload.cause);
}

function dayLabel(event: PublicReplayEvent): string {
  const round = (event.payload as { round_number?: number }).round_number;
  return i18n.t(isNightEvent(event) ? 'game.hpNightUnit' : 'game.hpDayUnit', { round: round ?? '?' });
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

// 虚拟列表的拍平行模型：tab 0 是「日分隔行 + 事件行」交错，其余 tab 只有事件行。
type ChronicleRow =
  | { kind: 'day'; label: string; key: string }
  | { kind: 'event'; event: PublicReplayEvent; index: number; key: string };

// 超过这个数量才走虚拟化：短列表维持原 map 渲染，零成本零风险。
const VIRTUALIZE_THRESHOLD = 120;

function DayDivider({ label }: { label: string }) {
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mt: 1.5, mb: 0.5 }}>
      <Box sx={{ flex: 1, height: '1px', bgcolor: 'divider' }} />
      <Typography variant="caption" sx={{ color: 'secondary.dark', fontWeight: 600, letterSpacing: 1.5 }}>
        {label}
      </Typography>
      <Box sx={{ flex: 1, height: '1px', bgcolor: 'divider' }} />
    </Box>
  );
}

function formatKnowledgeValue(key: string, value: unknown): string {
  const joiner = i18n.t('game.listJoin');
  const seatText = (seat: unknown) => i18n.t('game.seatNumber', { seat });
  if (key === 'teammates' && Array.isArray(value)) {
    return value.length ? value.map(seatText).join(joiner) : i18n.t('game.hpNone');
  }
  if (key === 'check_results' && Array.isArray(value)) {
    return value.length
      ? value.map((item) => {
          const record = item as Record<string, unknown>;
          const target = record.target_seat ?? record.target;
          const camp = record.camp ?? record.result;
          const campLabel = camp === 'werewolf' ? i18n.t('game.campWerewolf')
            : camp === 'good' ? i18n.t('game.campGood')
              : camp === 'third_party' ? i18n.t('game.campThird') : String(camp);
          return `${seatText(target)}→${campLabel}`;
        }).join(joiner)
      : i18n.t('game.hpNone');
  }
  if (key === 'last_wolf_kill_target' && typeof value === 'number') {
    return seatText(value);
  }
  if (typeof value === 'boolean') return value ? i18n.t('game.hpUsable') : i18n.t('game.hpUsed');
  return String(value);
}

function formatActionHistoryItem(item: Record<string, unknown>, L: LabelsModule): string {
  const round = item.round ?? '?';
  const action = item.action;
  if (action !== null && typeof action === 'object') {
    const record = action as Record<string, unknown>;
    const actionType = record.action_type ?? '?';
    const label = L.MEMORY_ACTION_LABELS[String(actionType)] ?? String(actionType);
    const targetSeat = record.target_seat;
    const target = targetSeat !== null && targetSeat !== undefined ? ` → ${i18n.t('game.seatNumber', { seat: targetSeat })}` : '';
    return i18n.t('game.hpAction', { round, label, target });
  }
  if (item.type === 'speech') {
    return i18n.t('game.hpSpeech', { round, text: item.text ?? '' });
  }
  if (item.type === 'vote') {
    const vote = item.vote;
    const targetSeat = vote !== null && typeof vote === 'object' ? (vote as Record<string, unknown>).target_seat : undefined;
    const target = targetSeat !== null && targetSeat !== undefined
      ? i18n.t('game.seatNumber', { seat: targetSeat })
      : i18n.t('game.abstain');
    return i18n.t('game.hpVote', { round, target });
  }
  return i18n.t('game.hpActionPhase', { round, phase: item.phase ?? '?' });
}

function formatWitnessedEvent(item: Record<string, unknown>, L: LabelsModule): string {
  const round = item.round ?? '?';
  const eventType = item.event ?? item.type ?? '?';
  const label = L.MEMORY_EVENT_LABELS[String(eventType)] ?? String(eventType);
  return i18n.t('game.hpWitness', { round, label });
}

function MemoryCard({ memory }: { memory: PlayerMemory }) {
  const { t } = useTranslation();
  const L = useLabels();
  const knowledgeEntries = Object.entries(memory.private_knowledge);
  return (
    <Box
      sx={{
        p: 1.2,
        mb: 0.6,
        borderRadius: 2,
        borderLeft: '3px solid',
        borderColor: memory.is_alive ? 'success.main' : 'text.disabled',
        bgcolor: INK_WASH,
      }}
    >
      <Typography variant="caption" sx={{ fontWeight: 600 }}>
        {t('game.seatNumber', { seat: memory.seat_number })} · {L.roleName(memory.role) ?? memory.role}
        {' · '}
        <Typography component="span" variant="caption" color={memory.is_alive ? 'success.light' : 'text.disabled'}>
          {t(memory.is_alive ? 'game.alive' : 'game.dead')}
        </Typography>
      </Typography>
      {knowledgeEntries.length > 0 && (
        <Box sx={{ mt: 0.4 }}>
          {knowledgeEntries.map(([key, value]) => (
            <Typography key={key} variant="caption" color="info.light" sx={{ display: 'block', fontSize: '0.7rem' }}>
              {L.KNOWLEDGE_LABELS[key] ?? key}: {formatKnowledgeValue(key, value)}
            </Typography>
          ))}
        </Box>
      )}
      {memory.action_history.length === 0 ? (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.4, fontSize: '0.7rem' }}>
          {t('game.hpNoActions')}
        </Typography>
      ) : (
        memory.action_history.map((item, index) => (
          <Typography
            key={`action-${index}`}
            variant="caption"
            color="text.secondary"
            sx={{ display: 'block', mt: 0.2, fontSize: '0.7rem' }}
          >
            {formatActionHistoryItem(item, L)}
          </Typography>
        ))
      )}
      {memory.witnessed_events.length === 0 ? (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.4, fontSize: '0.7rem' }}>
          {t('game.hpNoWitness')}
        </Typography>
      ) : (
        memory.witnessed_events.map((item, index) => (
          <Typography
            key={`witnessed-${index}`}
            variant="caption"
            color="text.secondary"
            sx={{ display: 'block', mt: 0.2, fontSize: '0.7rem' }}
          >
            {formatWitnessedEvent(item, L)}
          </Typography>
        ))
      )}
    </Box>
  );
}

const EventCard = memo(function EventCard({
  event,
  index,
  onSeek,
  rewoundRounds,
}: {
  event: PublicReplayEvent;
  index: number;
  onSeek: (index: number) => void;
  /** 被定序王子作废过的放逐轮次：由父级一次算好传入，免去每卡 O(n) 扫描。 */
  rewoundRounds: ReadonlySet<number>;
}) {
  const { t } = useTranslation();
  const L = useLabels();
  const players = useGameStore((s) => s.players);
  const onClick = useCallback(() => onSeek(index), [index, onSeek]);
  let content: React.ReactNode;

  switch (event.event_type) {
    case 'speaking':
      content = (
        <Typography variant="caption" sx={{ fontWeight: 500 }}>
          {t('game.evSpeaking', { seat: event.payload.seat })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'speech':
      content = (
        <>
          <Typography variant="caption" sx={{ fontWeight: 500 }}>
            {event.payload.phase === 'last_words'
              ? t('game.evLastWords', { seat: event.payload.player_seat })
              : event.payload.phase === 'sheriff_election'
                ? t('game.evCampaign', { seat: event.payload.player_seat })
                : t('game.evSpeechPlain', { seat: event.payload.player_seat })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
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
          {t('game.evVoteFor', { seat: event.payload.voter_seat, target: event.payload.target_seat === null ? t('game.abstain') : t('game.seatNumber', { seat: event.payload.target_seat }) })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'vote_result':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {t('game.acVoteResult', { round: event.payload.round_number })} · {event.payload.exiled_seat === null
            ? (rewoundRounds.has(event.payload.round_number)
              ? L.VOTE_REWOUND_RESULT
              : t('game.evTie'))
            : t('game.evExiled', { seat: event.payload.exiled_seat })}
        </Typography>
      );
      break;
    case 'vote_rewound':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {t('game.reportRewind', { seat: event.payload.seat })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'technical_abstain':
      content = (
        <Typography variant="caption" color="error.light" sx={{ fontWeight: 500 }}>
          {t('game.evSysAbstain', { seat: event.payload.voter_seat })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'exile_cancelled':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {t('game.evFlip', { seat: event.payload.target_seat })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'knight_duel':
      content = (
        <Typography variant="caption" color="error.light" sx={{ fontWeight: 500 }}>
          {t('game.acDuelTitle', { seat: event.payload.seat, target: event.payload.target_seat })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'fragrance_bond':
      content = (
        <Typography variant="caption" sx={{ color: 'secondary.main', fontWeight: 500 }}>
          {t('game.evEntangle', { seat: event.payload.seat, targets: event.payload.target_seats.join(t('game.listJoin')) })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'wraith_illusion':
      content = (
        <Typography variant="caption" sx={{ color: 'secondary.main', fontWeight: 500 }}>
          {event.payload.target_seat == null
            ? `${t('game.evIllusionStart', { seat: event.payload.seat })} · ${t('game.acRoundTargetMissing', { round: event.payload.round_number })}`
            : `${t('game.evIllusion', { seat: event.payload.seat, target: event.payload.target_seat })} · ${t('game.acRoundOnly', { round: event.payload.round_number })}`}
        </Typography>
      );
      break;
    case 'wolf_beauty_charm':
      content = (
        <Typography variant="caption" sx={{ color: 'secondary.main', fontWeight: 500 }}>
          {event.payload.target_seat == null
            ? `${t('game.evCharmStart', { seat: event.payload.seat })} · ${t('game.acRoundTargetMissing', { round: event.payload.round_number })}`
            : `${t('game.evCharm', { seat: event.payload.seat, target: event.payload.target_seat })} · ${t('game.acRoundOnly', { round: event.payload.round_number })}`}
        </Typography>
      );
      break;
    case 'wolf_beauty_revenge':
      content = (
        <Typography variant="caption" color="error.light" sx={{ fontWeight: 500 }}>
          {t('game.evRevenge', { seat: event.payload.seat, target: event.payload.target_seat })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'self_explode':
      content = (
        <Typography variant="caption" color="error.light" sx={{ fontWeight: 500 }}>
          {t('game.acExplodeSub', { round: event.payload.round_number })} · {t('game.evExplode', { seat: event.payload.seat, target: event.payload.target_seat })}
        </Typography>
      );
      break;
    case 'dance_pool':
      content = (
        <Typography variant="caption" color="primary.light" sx={{ fontWeight: 500 }}>
          {t('game.acDanceTitle', { seat: event.payload.seat, seats: event.payload.pool_seats.join(t('game.listJoin')) })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'mask_granted':
      content = (
        <Typography variant="caption" color="primary.light" sx={{ fontWeight: 500 }}>
          {t('game.evMask', { seat: event.payload.seat, target: event.payload.target_seat, inPool: t(event.payload.in_pool ? 'game.evInPool' : 'game.evNotInPool') })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'awakened_witch_assist_pick':
      content = (
        <Typography variant="caption" sx={{ color: ROLE_COLORS.witch.color, fontWeight: 500 }}>
          {t('game.evPoisonTarget', { target: event.payload.target_seat, assistants: event.payload.assistant_seats.map((seat) => t('game.seatNumber', { seat })).join(t('game.listJoin')) })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'awakened_witch_assist_chat':
      content = (
        <>
          <Typography variant="caption" sx={{ color: ROLE_COLORS.witch.color, fontWeight: 500 }}>
            {t('game.evAssistChat', { seat: event.payload.seat, brief: t('game.evPoisonTarget', { target: event.payload.target_seat, assistants: '' }) })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3, fontSize: '0.72rem' }} noWrap>
            {event.payload.text}
          </Typography>
        </>
      );
      break;
    case 'awakened_witch_assist_vote':
      content = (
        <>
          <Typography variant="caption" sx={{ color: ROLE_COLORS.witch.color, fontWeight: 500 }}>
            {t('game.evAssistVote', { seat: event.payload.seat, vote: L.awakenedWitchVoteLabel(event.payload.vote), target: event.payload.target_seat })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3, fontSize: '0.72rem' }} noWrap>
            {event.payload.reasoning || t('game.acNoReason')}
          </Typography>
        </>
      );
      break;
    case 'awakened_witch_poison_result':
      content = (
        <Typography variant="caption" sx={{ color: ROLE_COLORS.witch.color, fontWeight: 500 }}>
          {t('game.acPoisonResultTitle', { target: event.payload.target_seat, result: L.awakenedWitchPoisonResultLabel(event.payload.result) })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'crimson_explode':
      content = (
        <Typography variant="caption" color="error.light" sx={{ fontWeight: 500 }}>
          {t('game.acSilenceTitle', { seat: event.payload.seat })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'sheriff_elected':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {event.payload.seat == null
            ? `${t('game.evSheriffLost')} · ${t('game.acRoundOnly', { round: event.payload.round_number })}`
            : `${t('game.evSheriffElected', { seat: event.payload.seat })} · ${t('game.acRoundOnly', { round: event.payload.round_number })}`}
        </Typography>
      );
      break;
    case 'sheriff_badge':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {event.payload.to_seat == null
            ? `${t('game.evBadgeTear', { seat: event.payload.from_seat })} · ${t('game.acRoundOnly', { round: event.payload.round_number })}`
            : `${t('game.evBadgePass', { from: event.payload.from_seat, to: event.payload.to_seat })} · ${t('game.acRoundOnly', { round: event.payload.round_number })}`}
          {event.payload.reasoning ? t('game.acSheriffThought', { detail: event.payload.reasoning }) : ''}
        </Typography>
      );
      break;
    case 'sheriff_run':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {event.payload.choice === 'run'
            ? `${t('game.evRun', { seat: event.payload.seat })} · ${t('game.acRoundOnly', { round: event.payload.round_number })}`
            : `${t('game.evPassSeat', { seat: event.payload.seat })} · ${t('game.acRoundOnly', { round: event.payload.round_number })}`}
        </Typography>
      );
      break;
    case 'sheriff_withdraw':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {event.payload.choice === 'withdraw'
            ? `${t('game.evWithdraw', { seat: event.payload.seat })} · ${t('game.acRoundOnly', { round: event.payload.round_number })}`
            : `${t('game.evStay', { seat: event.payload.seat })} · ${t('game.acRoundOnly', { round: event.payload.round_number })}`}
        </Typography>
      );
      break;
    case 'sheriff_vote':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {`${t('game.seatNumber', { seat: event.payload.voter_seat })}${event.payload.kind === 'pk' ? t('game.acPkBallot') : ''} → ${
            event.payload.target_seat === null ? t('game.abstain') : t('game.seatNumber', { seat: event.payload.target_seat })
          } · ${t('game.acRoundOnly', { round: event.payload.round_number })}`}
        </Typography>
      );
      break;
    case 'sheriff_side':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {`${t('game.evSheriffSide', { seat: event.payload.seat, side: L.SHERIFF_SIDE_LABELS[event.payload.side] ?? event.payload.side })} · ${t('game.acRoundOnly', { round: event.payload.round_number })}`}
        </Typography>
      );
      break;
    case 'night_action':
      content = (
        <Typography variant="caption" color="info.light" sx={{ fontWeight: 500 }}>
          {L.nightActionCaption(event.payload.action_type, {
            seat: event.payload.seat,
            targetSeat: event.payload.target_seat,
            intendedSeat: event.payload.intended_seat,
            roleName: L.roleLabelForSeat(event.payload.seat, players),
          })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
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
    case 'commentary':
      content = (
        <>
          <Typography variant="caption" sx={{ color: ROLE_COLORS.guard.color, fontWeight: 600 }}>
            {event.payload.title}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3, fontSize: '0.72rem' }}>
            {event.payload.text}
          </Typography>
        </>
      );
      break;
    case 'wolf_chat_message':
      content = (
        <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.72rem' }}>
          {t('game.seatNumber', { seat: event.payload.seat })}：{event.payload.text}
          {event.payload.day_plan
            ? t('game.acDayPlan', { plan: event.payload.day_plan })
            : ''}
        </Typography>
      );
      break;
    case 'wolf_vote':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {t('game.seatNumber', { seat: event.payload.seat })} → {event.payload.target_seat === null ? t('game.abstain') : t('game.seatNumber', { seat: event.payload.target_seat })}
          （{event.payload.degraded ? `${L.DEGRADED_ACTION_LABEL}: ` : ''}{event.payload.reasoning}）
        </Typography>
      );
      break;
    case 'player_status':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {`${t('game.seatNumber', { seat: event.payload.player_seat })} ${
            L.PLAYER_STATUS_LABELS[event.payload.status] ?? event.payload.status
          } · ${t('game.acRoundOnly', { round: event.payload.round_number })}`}
        </Typography>
      );
      break;
    case 'witch_thought':
    case 'seer_thought':
      content = (
        <>
          <Typography variant="caption" color="info.light" sx={{ fontWeight: 500 }}>
            {t('game.seatNumber', { seat: event.payload.seat })}{L.THOUGHT_ROLE_NAMES[event.event_type]}{t('game.hpThinkingSuffix')}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3, fontSize: '0.72rem' }} noWrap>
            {event.payload.text}
          </Typography>
        </>
      );
      break;
    case 'night_thought': {
      const { action_type, seat, target_seat, reasoning, degraded } = event.payload;
      const who = L.thoughtActorName(action_type, {
        roleName: L.roleLabelForSeat(seat, players),
      });
      content = (
        <>
          <Typography variant="caption" color="info.light" sx={{ fontWeight: 500 }}>
            {degraded ? `${L.DEGRADED_ACTION_LABEL} · ${t('game.seatNumber', { seat })}` : `${t('game.seatNumber', { seat })}${who}${t('game.hpThinkingSuffix')}`}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.3, fontSize: '0.72rem' }} noWrap>
            {/* 代行动作没有「目标/不行动」可言，正文只留中性说明。 */}
            {degraded
              ? reasoning
              : target_seat === null ? t('game.acNoAction', { reason: reasoning }) : t('game.acTarget', { target: target_seat, reason: reasoning })}
          </Typography>
        </>
      );
      break;
    }
    case 'death':
      content = (
        <Typography variant="caption" color="error.light" sx={{ fontWeight: 500 }}>
          {t('game.evDeath', { seat: event.payload.player_seat, cause: L.DEATH_CAUSE_LABELS[event.payload.cause] })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'phase':
      content = (
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 500 }}>
          {t('game.hpPhaseChange', { phase: event.payload.phase })} · {t('game.acRoundOnly', { round: event.payload.round_number })}
        </Typography>
      );
      break;
    case 'winner':
      content = (
        <Typography variant="caption" color="warning.light" sx={{ fontWeight: 500 }}>
          {t('game.hpGameEnd')}
        </Typography>
      );
      break;
    default:
      // 只驱动 store、没有可见内容的类型（开局座位、翻牌、执行状态）不占位，
      // 否则编年史里会出现没有正文的空卡片。
      return null;
  }

  // 上表的键就是渲染面的全集（未知类型在上面的 default 已经返回 null），
  // 所以这里不需要「未知就写事件」的兜底。
  const tag = L.EVENT_TAGS[event.event_type];
  const tone = eventTone(event);

  return (
    <Box
      className="wk-chronicle-entry"
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
          zIndex: 1,
          transition: 'transform 0.15s',
        }}
      />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.8, mb: 0.3 }}>
        <Typography
          variant="caption"
          sx={{
            fontSize: '0.62rem',
            fontWeight: 600,
            letterSpacing: 1.5,
            lineHeight: 1.4,
            color: tone,
            border: '1px solid',
            borderColor: `color-mix(in srgb, ${tone} 40%, transparent)`,
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
});

export default function HistoryPanel({ onClose }: Props) {
  const { t } = useTranslation();
  const [tab, setTab] = useState(0);
  const [memories, setMemories] = useState<PlayerMemory[] | null>(null);
  const [memoriesError, setMemoriesError] = useState<string | null>(null);
  const timeline = useGameStore((s) => s.timeline);
  const seekTo = useGameStore((s) => s.seekTo);
  const pause = useGameStore((s) => s.pause);
  const gameId = useGameStore((s) => s.gameId);
  const indexedEvents = useMemo(
    () => timeline.map((event, index) => ({ event, index })).reverse(),
    [timeline],
  );
  const speeches = useMemo(() => indexedEvents.filter(({ event }) => (
    event.event_type === 'speech' || event.event_type === 'sheriff_side'
  )), [indexedEvents]);
  const votes = useMemo(() => indexedEvents.filter(({ event }) => (
    event.event_type === 'vote'
    || event.event_type === 'vote_result'
    || event.event_type === 'vote_rewound'
    || event.event_type === 'exile_cancelled'
    || event.event_type === 'sheriff_elected'
    || event.event_type === 'sheriff_badge'
    || event.event_type === 'sheriff_run'
    || event.event_type === 'sheriff_withdraw'
    || event.event_type === 'sheriff_vote'
  )), [indexedEvents]);
  const deaths = useMemo(() => indexedEvents.filter(({ event }) => (
    event.event_type === 'death' || event.event_type === 'self_explode'
  )), [indexedEvents]);
  const nightActions = useMemo(() => indexedEvents.filter(({ event }) => (
    event.event_type === 'night_action' || event.event_type === 'wolf_vote'
    || event.event_type === 'narration'
    // 舞池/面具是夜间行动，只是各自带自己的载荷，所以有自己的事件类型。
    || event.event_type === 'dance_pool' || event.event_type === 'mask_granted'
    // 觉醒女巫的毒药调配也发生在夜里。
    || event.event_type === 'awakened_witch_assist_pick'
    || event.event_type === 'awakened_witch_assist_chat'
    || event.event_type === 'awakened_witch_assist_vote'
    || event.event_type === 'awakened_witch_poison_result'
  )), [indexedEvents]);
  const thoughts = useMemo(() => indexedEvents.filter(({ event }) => (
    event.event_type === 'wolf_chat_message' || event.event_type === 'witch_thought' || event.event_type === 'seer_thought' || event.event_type === 'night_thought'
  )), [indexedEvents]);
  const currentEvents = tab === 0 ? indexedEvents
    : tab === 1 ? speeches
    : tab === 2 ? votes
    : tab === 3 ? deaths
    : tab === 4 ? nightActions
    : thoughts;

  // 被作废的放逐轮次一次算全：卡片只查 Set，不再各自扫整条时间轴。
  const rewoundRounds = useMemo(
    () => new Set(
      timeline
        .filter((event): event is Extract<PublicReplayEvent, { event_type: 'vote_rewound' }> => (
          event.event_type === 'vote_rewound'
        ))
        .map((event) => event.payload.round_number),
    ),
    [timeline],
  );

  // 拍平成虚拟列表行：tab 0 在事件行之间插日分隔行，其余 tab 纯事件行。
  const rows = useMemo<ChronicleRow[]>(() => {
    if (tab === 0) {
      const flat: ChronicleRow[] = [];
      for (const group of buildDayGroups(currentEvents)) {
        flat.push({ kind: 'day', label: group.label, key: `day-${flat.length}` });
        for (const { event, index } of group.items) {
          flat.push({ kind: 'event', event, index, key: `e-${index}` });
        }
      }
      return flat;
    }
    return currentEvents.map(({ event, index }) => ({
      kind: 'event', event, index, key: `e-${index}`,
    }));
  }, [currentEvents, tab]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 64,
    overscan: 8,
    // jsdom 没有布局，给个非零初始矩形让首屏虚拟项能挂出来（测试也依赖它）。
    initialRect: { width: 320, height: 600 },
  });
  const virtualItems = virtualizer.getVirtualItems();
  const virtualized = currentEvents.length > VIRTUALIZE_THRESHOLD;

  useEffect(() => {
    if (tab !== 6 || gameId === null) return undefined;
    let cancelled = false;
    fetchGameMemories(gameId)
      .then((result) => {
        if (!cancelled) setMemories(result.memories);
      })
      .catch(() => {
        if (!cancelled) setMemoriesError(t('game.memoryLoadFailed'));
      });
    return () => { cancelled = true; };
  }, [tab, gameId, t]);

  const handleTabChange = (_: React.SyntheticEvent, value: number) => {
    if (value === 6) {
      setMemories(null);
      setMemoriesError(null);
    }
    setTab(value);
  };

  const handleClick = useCallback((index: number) => {
    seekTo(index);
    pause();
  }, [seekTo, pause]);

  return (
    <Box sx={{ width: 320, flexShrink: 0, display: 'flex', flexDirection: 'column', borderLeft: '1px solid', borderColor: 'divider', bgcolor: 'background.paper', overflow: 'hidden' }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', px: 2, py: 1.2, borderBottom: '1px solid', borderColor: 'divider' }}>
        <Typography variant="body1" sx={{ fontWeight: 500 }}>{t('game.hpTitle')}</Typography>
        <IconButton aria-label={t('game.hpClose')} onClick={onClose} size="small"><CloseIcon fontSize="small" /></IconButton>
      </Box>

      <Tabs value={tab} onChange={handleTabChange} variant="scrollable" scrollButtons={false} sx={{ borderBottom: '1px solid', borderColor: 'divider', minHeight: 40, '& .MuiTab-root': { minHeight: 40, py: 0.5 } }}>
        <Tab label={t('game.hpTabAll', { count: indexedEvents.length })} />
        <Tab label={t('game.hpTabSpeech', { count: speeches.length })} />
        <Tab label={t('game.hpTabVote', { count: votes.length })} />
        <Tab label={t('game.hpTabDeath', { count: deaths.length })} />
        <Tab label={t('game.hpTabNight', { count: nightActions.length })} />
        <Tab label={t('game.hpTabThought', { count: thoughts.length })} />
        <Tab label={t('game.hpTabMemory')} />
      </Tabs>

      <Box ref={scrollRef} sx={{ flex: 1, overflowY: 'auto', px: 1.2, py: 1 }}>
        {tab === 6 ? (
          <>
            {memoriesError !== null && (
              <Typography variant="body2" color="error" sx={{ textAlign: 'center', py: 4 }}>
                {memoriesError}
              </Typography>
            )}
            {memories === null && memoriesError === null && (
              <Typography variant="body2" color="text.disabled" sx={{ textAlign: 'center', py: 4 }}>
                {t('game.hpLoadingMemory')}
              </Typography>
            )}
            {memories !== null && memories.length === 0 && (
              <Typography variant="body2" color="text.disabled" sx={{ textAlign: 'center', py: 4 }}>
                {t('game.hpNoMemory')}
              </Typography>
            )}
            {memories !== null && memories.map((memory) => (
              <MemoryCard key={memory.seat_number} memory={memory} />
            ))}
          </>
        ) : virtualized ? (
          <Box sx={{ height: virtualizer.getTotalSize(), position: 'relative', width: '100%' }}>
            {virtualItems.map((virtualRow) => {
              const row = rows[virtualRow.index];
              return (
                <Box
                  key={row.key}
                  data-index={virtualRow.index}
                  ref={virtualizer.measureElement}
                  sx={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: '100%',
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                >
                  {row.kind === 'day'
                    ? <DayDivider label={row.label} />
                    : (
                      <EventCard
                        event={row.event}
                        index={row.index}
                        onSeek={handleClick}
                        rewoundRounds={rewoundRounds}
                      />
                    )}
                </Box>
              );
            })}
          </Box>
        ) : (
          <>
            {tab === 0 ? (
              buildDayGroups(currentEvents).map((group, groupIndex) => (
                <Box key={`chronicle-${groupIndex}`} sx={{ mb: 0.5 }}>
                  <DayDivider label={group.label} />
                  {group.items.map(({ event, index }) => (
                    <EventCard
                      key={index}
                      event={event}
                      index={index}
                      onSeek={handleClick}
                      rewoundRounds={rewoundRounds}
                    />
                  ))}
                </Box>
              ))
            ) : (
              currentEvents.map(({ event, index }) => (
                <EventCard
                  key={index}
                  event={event}
                  index={index}
                  onSeek={handleClick}
                  rewoundRounds={rewoundRounds}
                />
              ))
            )}
            {currentEvents.length === 0 && (
              <Typography variant="body2" color="text.disabled" sx={{ textAlign: 'center', py: 4 }}>
                {t('game.hpNoRecords')}
              </Typography>
            )}
          </>
        )}
      </Box>
    </Box>
  );
}
