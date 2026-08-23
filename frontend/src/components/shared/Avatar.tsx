import { Avatar as MuiAvatar } from '@mui/material';
import { AVATAR_PALETTE, INK } from '../../theme/tokens';

interface Props {
  seat: number;
  size?: number;
  isAlive?: boolean;
}

export default function Avatar({ seat, size = 48, isAlive = true }: Props) {
  const color = AVATAR_PALETTE[(seat - 1) % AVATAR_PALETTE.length];
  return (
    <MuiAvatar
      sx={{
        width: size,
        height: size,
        bgcolor: color,
        color: INK.primary,
        border: '1px solid rgba(212,168,83,0.25)',
        opacity: isAlive ? 1 : 0.35,
        fontSize: size * 0.42,
        fontWeight: 700,
        transition: 'opacity 0.3s',
      }}
    >
      {seat}
    </MuiAvatar>
  );
}
