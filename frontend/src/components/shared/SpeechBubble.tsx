import { Box, Typography } from '@mui/material';

interface Props {
  seat: number;
  text: string;
}

export default function SpeechBubble({ seat, text }: Props) {
  return (
    <Box
      sx={{
        p: 1.5,
        mb: 1,
        borderLeft: '3px solid',
        borderColor: 'primary.main',
        borderRadius: '0 12px 12px 0',
        bgcolor: 'transparent',
      }}
    >
      <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 500 }}>
        {seat}号
      </Typography>
      <Typography variant="body2" color="text.primary" sx={{ mt: 0.5, whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
        {text}
      </Typography>
    </Box>
  );
}
