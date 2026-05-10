import { Box, Typography, keyframes } from '@mui/material';
import { useGameStore } from '../../store/gameStore';
import { TimelineEntry } from '../../store/types';

const fadeIn = keyframes`
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
`;

const STEP_LABELS: Record<string, string> = {
  werewolf_open: '狼人请睁眼',
  werewolf_vote: '狼人投票中',
  werewolf_target: '狼人已锁定目标',
  werewolf_close: '狼人请闭眼',
  witch_open: '女巫请睁眼',
  witch_action: '女巫行动中',
  witch_close: '女巫请闭眼',
  seer_open: '预言家请睁眼',
  seer_check: '预言家查验中',
  seer_close: '预言家请闭眼',
};

const PHASE_LABELS: Record<string, string> = {
  waiting: '等待游戏开始',
  role_deal: '正在分配角色',
  night: '天黑请闭眼',
  dawn: '天亮了',
  last_words: '遗言阶段',
  speech: '发言阶段',
  vote_casting: '投票阶段',
  vote_resolution: '公布投票结果',
  game_over: '游戏结束',
};

const CAUSE_LABELS: Record<string, string> = {
  wolf_kill: '被狼人杀害',
  poison: '被毒杀',
  exile: '被放逐',
  hunter_shot: '被猎人带走',
  self_explode: '自爆',
  love_death: '殉情',
};

const panelSx = {
  textAlign: 'center',
  py: 2,
  px: 3,
  animation: `${fadeIn} 0.25s ease-out`,
};

export default function CenterDisplay() {
  const { timeline, timelineIndex, isPaused, phase, roundNumber } = useGameStore();

  const entry: TimelineEntry | null = timelineIndex >= 0 && timelineIndex < timeline.length
    ? timeline[timelineIndex]
    : null;

  if (isPaused && !entry) {
    return (
      <Box sx={{ textAlign: 'center', py: 2 }}>
        <Typography variant="subtitle1" color="text.secondary">⏸ 游戏已暂停</Typography>
        <Typography variant="caption" color="text.disabled" sx={{ mt: 0.5, display: 'block' }}>
          点击播放按钮继续
        </Typography>
      </Box>
    );
  }

  if (!entry) {
    return (
      <Box sx={{ textAlign: 'center', py: 4 }}>
        <Typography variant="h6" color="text.secondary" fontWeight={500}>
          {PHASE_LABELS[phase] || phase}
        </Typography>
        <Typography variant="body2" color="text.disabled" sx={{ mt: 0.5 }}>
          第 {roundNumber} 轮
        </Typography>
      </Box>
    );
  }

  const key = `entry-${timelineIndex}`;

  // ── Conversation entries ────────────────────────────────

  if (entry.type === 'conversation') {
    const scope = entry.scope || 'public';

    if (scope === 'public') {
      return (
        <Box key={key} sx={panelSx}>
          <Typography variant="subtitle2" color="primary.light" gutterBottom>
            {entry.speaker_seat != null ? `${entry.speaker_seat}号` : '📢 系统'}
            {entry.speaker_role ? ` ${entry.speaker_role.replace('wolf-killer-', '')}` : ''}
          </Typography>
          <Typography
            variant="body2"
            color="grey.400"
            sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 260, overflowY: 'auto', lineHeight: 1.8, fontSize: '0.875rem' }}
          >
            {entry.content}
          </Typography>
        </Box>
      );
    }

    if (scope === 'werewolf') {
      return (
        <Box key={key} sx={{ ...panelSx, bgcolor: 'rgba(242,184,181,0.06)', borderRadius: 2 }}>
          <Typography variant="subtitle2" color="error.light" gutterBottom>
            🐺 狼人频道 · {entry.speaker_seat}号
          </Typography>
          <Typography
            variant="body2"
            color="grey.400"
            sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 260, overflowY: 'auto', lineHeight: 1.8, fontSize: '0.875rem' }}
          >
            {entry.content}
          </Typography>
        </Box>
      );
    }

    if (scope === 'night_intel') {
      const visible = entry.visible_to?.join('、') || '?';
      return (
        <Box key={key} sx={{ ...panelSx, bgcolor: 'rgba(196,181,253,0.06)', borderRadius: 2 }}>
          <Typography variant="subtitle2" color="secondary.light" gutterBottom>
            📋 夜间情报 · 仅 {visible} 号可见
          </Typography>
          <Typography
            variant="body2"
            color="grey.400"
            sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 260, overflowY: 'auto', lineHeight: 1.8, fontSize: '0.875rem' }}
          >
            {entry.content}
          </Typography>
        </Box>
      );
    }
  }

  // ── Operation entries ───────────────────────────────────

  if (entry.type === 'operation') {
    const op = entry.operation || '';

    // Night substep
    if (op.startsWith('werewolf_') || op.startsWith('witch_') || op.startsWith('seer_') || op.startsWith('hunter_')) {
      const stepLabel = STEP_LABELS[op] || op;
      const detail = entry.data || {};
      return (
        <Box key={key} sx={panelSx}>
          <Typography variant="subtitle1" fontWeight={500} color="grey.300">
            🌙 {stepLabel}
          </Typography>
          {detail.target && (
            <Typography variant="body2" color="error.light" sx={{ mt: 0.5 }}>
              目标: {detail.target}号
            </Typography>
          )}
          {detail.result && (
            <Typography variant="body2" color="grey.400" sx={{ mt: 0.3 }}>
              结果: {detail.result}
            </Typography>
          )}
        </Box>
      );
    }

    switch (op) {
      case 'vote':
        return (
          <Box key={key} sx={panelSx}>
            <Typography variant="subtitle2" color="warning.light" gutterBottom>
              {entry.seat}号 投票
            </Typography>
            {entry.data?.reasoning && (
              <Typography variant="body2" color="grey.500" sx={{ fontStyle: 'italic', mb: 0.5 }}>
                「{entry.data.reasoning}」
              </Typography>
            )}
            <Typography variant="body2" color="grey.300">
              → {entry.data?.target ? `${entry.data.target}号` : '弃权'}
            </Typography>
          </Box>
        );

      case 'vote_result':
        return (
          <Box key={key} sx={{ ...panelSx, py: 1.8 }}>
            <Typography variant="subtitle1" color="warning.light" gutterBottom>
              📊 投票结果
            </Typography>
            <Typography variant="body2" color="grey.300">
              {entry.data?.exiled != null
                ? `${entry.data.exiled}号玩家被放逐出局`
                : '平票，无人被放逐'}
            </Typography>
          </Box>
        );

      case 'night_deaths':
        return (
          <Box key={key} sx={{ ...panelSx, py: 1.8 }}>
            <Typography variant="subtitle1" color="error.light" gutterBottom>
              ☀ 天亮死讯
            </Typography>
            {(entry.data?.deaths as any[])?.map((d: any) => {
              const seat = d.player_seat || d.seat;
              return (
                <Typography key={seat} variant="body2" color="grey.300" sx={{ lineHeight: 1.7 }}>
                  {seat != null ? `${seat}号` : '玩家'} {CAUSE_LABELS[d.cause] || d.cause || '死亡'}
                </Typography>
              );
            }) || (
              <Typography variant="body2" color="grey.300">
                {entry.data?.content || '平安夜，无人死亡'}
              </Typography>
            )}
          </Box>
        );

      case 'phase_change':
        return (
          <Box key={key} sx={{ ...panelSx, py: 2.5 }}>
            <Typography variant="h6" color="text.secondary" fontWeight={500}>
              {PHASE_LABELS[entry.phase] || entry.phase}
            </Typography>
            <Typography variant="body2" color="text.disabled" sx={{ mt: 0.3 }}>
              第 {entry.round || roundNumber} 轮
            </Typography>
          </Box>
        );

      case 'game_over':
        return (
          <Box key={key} sx={{ ...panelSx, py: 1.8 }}>
            <Typography variant="h6" color="warning.light">
              游戏结束
            </Typography>
            <Typography variant="body2" color="grey.300">
              {entry.data?.winner === 'werewolf' ? '🐺 狼人阵营' : '🛡 好人阵营'} 获胜
            </Typography>
          </Box>
        );

      default:
        break;
    }
  }

  return (
    <Box sx={{ textAlign: 'center', py: 2 }}>
      <Typography variant="body2" color="text.disabled">
        第 {roundNumber} 轮 · {PHASE_LABELS[phase] || phase}
      </Typography>
    </Box>
  );
}
