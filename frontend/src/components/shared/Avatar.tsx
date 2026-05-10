import { Avatar as MuiAvatar } from '@mui/material';

const COLORS = [
  '#4285F4', '#EA4335', '#FBBC04', '#34A853',
  '#FF6D01', '#46BDC6', '#7BA7F0', '#F06292', '#81C784',
];

interface Props {
  seat: number;
  size?: number;
  isAlive?: boolean;
}

export default function Avatar({ seat, size = 48, isAlive = true }: Props) {
  const color = COLORS[(seat - 1) % COLORS.length];
  return (
    <MuiAvatar
      sx={{
        width: size,
        height: size,
        bgcolor: color,
        opacity: isAlive ? 1 : 0.35,
        fontSize: size * 0.42,
        fontWeight: 500,
        transition: 'opacity 0.3s',
      }}
    >
      {seat}
    </MuiAvatar>
  );
}
