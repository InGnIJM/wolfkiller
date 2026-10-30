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
import RecordVoiceOver from '@mui/icons-material/RecordVoiceOver';
import Shield from '@mui/icons-material/Shield';
import Flight from '@mui/icons-material/Flight';
import Link from '@mui/icons-material/Link';
import Loyalty from '@mui/icons-material/Loyalty';
import type { SvgIconComponent } from '@mui/icons-material';
import { INK, ROLE_COLORS } from '../../theme/tokens';
import { useTranslation } from 'react-i18next';
import { useLabels } from '../../i18n/useLabels';

interface Props {
  role: string | null;
  size?: number;
}

const ROLE_ICONS: Record<string, { Icon: SvgIconComponent; color: string }> = {
  'wolf-killer-werewolf': { Icon: Pets, color: ROLE_COLORS.werewolf.color },
  'wolf-killer-villager': { Icon: Person, color: ROLE_COLORS.villager.color },
  'wolf-killer-seer': { Icon: Visibility, color: ROLE_COLORS.seer.color },
  'wolf-killer-witch': { Icon: Science, color: ROLE_COLORS.witch.color },
  'wolf-killer-awakened-witch': { Icon: Science, color: ROLE_COLORS.witch.color },
  'wolf-killer-hunter': { Icon: GpsFixed, color: ROLE_COLORS.hunter.color },
  'wolf-killer-guard': { Icon: Security, color: ROLE_COLORS.guard.color },
  'wolf-killer-idiot': { Icon: Mood, color: ROLE_COLORS.idiot.color },
  'wolf-killer-werewolf-king': { Icon: WorkspacePremium, color: ROLE_COLORS.werewolf_king.color },
  'wolf-killer-wolf-king': { Icon: LocalFireDepartment, color: ROLE_COLORS.werewolf_king.color },
  'wolf-killer-awakened-wolf-king': { Icon: LocalFireDepartment, color: ROLE_COLORS.werewolf_king.color },
  'wolf-killer-evil-knight': { Icon: NightsStay, color: ROLE_COLORS.werewolf.color },
  'wolf-killer-dream-weaver': { Icon: Bedtime, color: ROLE_COLORS.guard.color },
  'wolf-killer-gargoyle': { Icon: Fort, color: ROLE_COLORS.werewolf.color },
  'wolf-killer-gravekeeper': { Icon: Church, color: ROLE_COLORS.guard.color },
  'wolf-killer-knight': { Icon: Gavel, color: ROLE_COLORS.knight.color },
  'wolf-killer-wolf-beauty': { Icon: Favorite, color: ROLE_COLORS.wolf_beauty.color },
  'wolf-killer-old-drunkard': { Icon: LocalBar, color: ROLE_COLORS.old_drunkard.color },
  'wolf-killer-lonely-girl': { Icon: FavoriteBorder, color: ROLE_COLORS.villager.color },
  'wolf-killer-awakened-seer': { Icon: AutoAwesome, color: ROLE_COLORS.seer.color },
  'wolf-killer-fragrance-phantom': { Icon: Air, color: ROLE_COLORS.werewolf.color },
  'wolf-killer-miracle-merchant': { Icon: Storefront, color: ROLE_COLORS.hunter.color },
  'wolf-killer-mirror-girl': { Icon: Flip, color: ROLE_COLORS.seer.color },
  'wolf-killer-awakened-hidden-wolf': { Icon: Masks, color: ROLE_COLORS.werewolf.color },
  'wolf-killer-radiance-earl': { Icon: LightMode, color: ROLE_COLORS.guard.color },
  'wolf-killer-eclipse-maid': { Icon: DarkMode, color: ROLE_COLORS.werewolf.color },
  'wolf-killer-time-wolf': { Icon: HourglassBottom, color: ROLE_COLORS.werewolf.color },
  'wolf-killer-order-prince': { Icon: Replay, color: ROLE_COLORS.knight.color },
  'wolf-killer-fool-seer': { Icon: RecordVoiceOver, color: ROLE_COLORS.seer.color },
  'wolf-killer-iron-villager': { Icon: Shield, color: ROLE_COLORS.villager.color },
  'wolf-killer-crow': { Icon: Flight, color: ROLE_COLORS.villager.color },
  'wolf-killer-cupid': { Icon: Link, color: ROLE_COLORS.guard.color },
  'wolf-killer-admirer': { Icon: Loyalty, color: ROLE_COLORS.villager.color },
  'wolf-killer-awakened-hunter': { Icon: GpsFixed, color: ROLE_COLORS.hunter.color },
  'wolf-killer-awakened-wolf-beauty': { Icon: Favorite, color: ROLE_COLORS.wolf_beauty.color },
  'wolf-killer-crimson-envoy': { Icon: LocalFireDepartment, color: ROLE_COLORS.werewolf.color },
  'wolf-killer-dancer': { Icon: AutoAwesome, color: ROLE_COLORS.guard.color },
  'wolf-killer-demon-hunter': { Icon: GpsFixed, color: ROLE_COLORS.hunter.color },
  'wolf-killer-mask': { Icon: Masks, color: ROLE_COLORS.werewolf.color },
  'wolf-killer-nightmare': { Icon: Bedtime, color: ROLE_COLORS.werewolf.color },
  'wolf-killer-pure-white': { Icon: Visibility, color: ROLE_COLORS.seer.color },
  'wolf-killer-wolf-witch': { Icon: Science, color: ROLE_COLORS.werewolf.color },
};

// 统一使用 Material Icons（@mui/icons-material），禁止 emoji 作为结构图标
export default function RoleIcon({ role, size = 28 }: Props) {
  const { t } = useTranslation();
  const L = useLabels();
  if (!role) return null;
  const entry = ROLE_ICONS[role];
  const Icon = entry ? entry.Icon : Help;
  const label = L.roleName(role) ?? t('common.unknownRole');
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
