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
