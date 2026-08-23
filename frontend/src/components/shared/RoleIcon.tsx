import GpsFixed from '@mui/icons-material/GpsFixed';
import Help from '@mui/icons-material/Help';
import Person from '@mui/icons-material/Person';
import Pets from '@mui/icons-material/Pets';
import Science from '@mui/icons-material/Science';
import Security from '@mui/icons-material/Security';
import Visibility from '@mui/icons-material/Visibility';
import type { SvgIconComponent } from '@mui/icons-material';
import { INK, ROLE_COLORS } from '../../theme/tokens';

interface Props {
  role: string | null;
  size?: number;
}

const ROLE_ICONS: Record<string, { Icon: SvgIconComponent; label: string; color: string }> = {
  'wolf-killer-werewolf': { Icon: Pets, label: '狼人', color: ROLE_COLORS.werewolf.color },
  'wolf-killer-villager': { Icon: Person, label: '村民', color: ROLE_COLORS.villager.color },
  'wolf-killer-seer': { Icon: Visibility, label: '预言家', color: ROLE_COLORS.seer.color },
  'wolf-killer-witch': { Icon: Science, label: '女巫', color: ROLE_COLORS.witch.color },
  'wolf-killer-hunter': { Icon: GpsFixed, label: '猎人', color: ROLE_COLORS.hunter.color },
  'wolf-killer-guard': { Icon: Security, label: '守卫', color: ROLE_COLORS.guard.color },
};

// 统一使用 Material Icons（@mui/icons-material），禁止 emoji 作为结构图标
export default function RoleIcon({ role, size = 28 }: Props) {
  if (!role) return null;
  const entry = ROLE_ICONS[role];
  const Icon = entry ? entry.Icon : Help;
  const label = entry ? entry.label : '未知身份';
  const color = entry ? entry.color : INK.faint;
  return (
    <span
      role="img"
      aria-label={label}
      title={role}
      style={{ display: 'inline-flex', lineHeight: 1 }}
    >
      <Icon sx={{ fontSize: size, color }} />
    </span>
  );
}
