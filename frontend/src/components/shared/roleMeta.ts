import { ROLE_ART, ROLE_COLORS } from '../../theme/tokens';
import type { RoleColorKey } from '../../theme/tokens';

export interface RoleMetaEntry {
  label: string;
  color: string;
  bg: string;
  art: string;
}

const ROLE_ROWS: readonly { id: string; key: RoleColorKey; label: string }[] = [
  { id: 'wolf-killer-werewolf', key: 'werewolf', label: '狼人' },
  { id: 'wolf-killer-witch', key: 'witch', label: '女巫' },
  { id: 'wolf-killer-seer', key: 'seer', label: '预言家' },
  { id: 'wolf-killer-hunter', key: 'hunter', label: '猎人' },
  { id: 'wolf-killer-villager', key: 'villager', label: '村民' },
  { id: 'wolf-killer-guard', key: 'guard', label: '守卫' },
  { id: 'wolf-killer-idiot', key: 'idiot', label: '白痴' },
  { id: 'wolf-killer-werewolf-king', key: 'werewolf_king', label: '白狼王' },
  // 狼王与白狼王共用狼王配色与立绘（同一「狼王」视觉家族），靠 label 与 RoleIcon
  // 的图标区分。两个 id 只差词序，任何地方都不许简写。
  { id: 'wolf-killer-wolf-king', key: 'werewolf_king', label: '狼王' },
  { id: 'wolf-killer-evil-knight', key: 'werewolf', label: '恶夜骑士' },
  { id: 'wolf-killer-dream-weaver', key: 'guard', label: '摄梦人' },
  { id: 'wolf-killer-knight', key: 'knight', label: '骑士' },
  { id: 'wolf-killer-wolf-beauty', key: 'wolf_beauty', label: '狼美人' },
  { id: 'wolf-killer-old-drunkard', key: 'old_drunkard', label: '老酒鬼' },
];

export const ROLE_META: Record<string, RoleMetaEntry> = Object.fromEntries(
  ROLE_ROWS.map(({ id, key, label }) => [id, {
    label,
    color: ROLE_COLORS[key].color,
    bg: ROLE_COLORS[key].bg,
    art: ROLE_ART[key],
  }]),
);

export function roleMetaFor(roleId: string | null | undefined): RoleMetaEntry | undefined {
  if (!roleId) return undefined;
  return ROLE_META[roleId];
}
