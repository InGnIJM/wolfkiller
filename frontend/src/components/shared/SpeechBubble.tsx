import { Box, Typography } from '@mui/material';

interface Props {
  seat: number;
  text: string;
  isAlive?: boolean;
  scope?: 'public' | 'werewolf' | 'night_intel';
}

export default function SpeechBubble({ seat, text, isAlive = true, scope = 'public' }: Props) {
  const borderColor = scope === 'werewolf'
    ? 'error.main'
    : scope === 'night_intel'
      ? 'secondary.main'
      : 'primary.main';

  const bgcolor = scope === 'werewolf'
    ? 'rgba(242,184,181,0.06)'
    : scope === 'night_intel'
      ? 'rgba(196,181,253,0.06)'
      : 'transparent';

  const icon = scope === 'werewolf' ? '🐺 ' : scope === 'night_intel' ? '📋 ' : '';

  return (
    <Box
      sx={{
        p: 1.5,
        mb: 1,
        opacity: isAlive ? 1 : 0.55,
        borderLeft: '3px solid',
        borderColor,
        borderRadius: '0 12px 12px 0',
        bgcolor,
        transition: 'opacity 0.3s',
      }}
    >
      <Typography variant="caption" color="text.secondary" fontWeight={500}>
        {icon}{seat}号 {!isAlive ? '(遗言)' : ''}
      </Typography>
      <Typography variant="body2" color="text.primary" sx={{ mt: 0.5, whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
        {text}
      </Typography>
    </Box>
  );
}
