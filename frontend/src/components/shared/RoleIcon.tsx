import Gavel from '@mui/icons-material/Gavel';
import Bedtime from '@mui/icons-material/Bedtime';
import Church from '@mui/icons-material/Church';
import Fort from '@mui/icons-material/Fort';
import NightsStay from '@mui/icons-material/NightsStay';
import GpsFixed from '@mui/icons-material/GpsFixed';
import Help from '@mui/icons-material/Help';
import LocalFireDepartment from '@mui/icons-material/LocalFireDepartment';
import Mood from '@mui/icons-material/Mood';
import WorkspacePremium from '@mui/icons-material/WorkspacePremium';
import Person from '@mui/icons-material/Person';
import Pets from '@mui/icons-material/Pets';
import Science from '@mui/icons-material/Science';
import Security from '@mui/icons-material/Security';
import LocalBar from '@mui/icons-material/LocalBar';
import Favorite from '@mui/icons-material/Favorite';
import FavoriteBorder from '@mui/icons-material/FavoriteBorder';
import AutoAwesome from '@mui/icons-material/AutoAwesome';
import Air from '@mui/icons-material/Air';
import Storefront from '@mui/icons-material/Storefront';
import Flip from '@mui/icons-material/Flip';
import Masks from '@mui/icons-material/Masks';
import LightMode from '@mui/icons-material/LightMode';
import DarkMode from '@mui/icons-material/DarkMode';
import HourglassBottom from '@mui/icons-material/HourglassBottom';
import Replay from '@mui/icons-material/Replay';
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
  'wolf-killer-idiot': { Icon: Mood, label: '白痴', color: ROLE_COLORS.idiot.color },
  'wolf-killer-werewolf-king': { Icon: WorkspacePremium, label: '白狼王', color: ROLE_COLORS.werewolf_king.color },
  'wolf-killer-wolf-king': { Icon: LocalFireDepartment, label: '狼王', color: ROLE_COLORS.werewolf_king.color },
  'wolf-killer-evil-knight': { Icon: NightsStay, label: '恶夜骑士', color: ROLE_COLORS.werewolf.color },
  'wolf-killer-dream-weaver': { Icon: Bedtime, label: '摄梦人', color: ROLE_COLORS.guard.color },
  'wolf-killer-gargoyle': { Icon: Fort, label: '石像鬼', color: ROLE_COLORS.werewolf.color },
  'wolf-killer-gravekeeper': { Icon: Church, label: '守墓人', color: ROLE_COLORS.guard.color },
  'wolf-killer-knight': { Icon: Gavel, label: '骑士', color: ROLE_COLORS.knight.color },
  'wolf-killer-wolf-beauty': { Icon: Favorite, label: '狼美人', color: ROLE_COLORS.wolf_beauty.color },
  'wolf-killer-old-drunkard': { Icon: LocalBar, label: '老酒鬼', color: ROLE_COLORS.old_drunkard.color },
  'wolf-killer-lonely-girl': { Icon: FavoriteBorder, label: '孤独少女', color: ROLE_COLORS.villager.color },
  'wolf-killer-awakened-seer': { Icon: AutoAwesome, label: '觉醒预言家', color: ROLE_COLORS.seer.color },
  'wolf-killer-fragrance-phantom': { Icon: Air, label: '寻香魅影', color: ROLE_COLORS.werewolf.color },
  'wolf-killer-miracle-merchant': { Icon: Storefront, label: '奇迹商人', color: ROLE_COLORS.hunter.color },
  'wolf-killer-mirror-girl': { Icon: Flip, label: '魔镜少女', color: ROLE_COLORS.seer.color },
  'wolf-killer-awakened-hidden-wolf': { Icon: Masks, label: '觉醒隐狼', color: ROLE_COLORS.werewolf.color },
  'wolf-killer-radiance-earl': { Icon: LightMode, label: '流光伯爵', color: ROLE_COLORS.guard.color },
  'wolf-killer-eclipse-maid': { Icon: DarkMode, label: '蚀日侍女', color: ROLE_COLORS.werewolf.color },
  'wolf-killer-time-wolf': { Icon: HourglassBottom, label: '蚀时狼妃', color: ROLE_COLORS.werewolf.color },
  'wolf-killer-order-prince': { Icon: Replay, label: '定序王子', color: ROLE_COLORS.knight.color },
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
