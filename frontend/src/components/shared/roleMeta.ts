import { roleArt, ROLE_COLORS } from '../../theme/tokens';
import type { RoleColorKey } from '../../theme/tokens';

export interface RoleMetaEntry {
  label: string;
  color: string;
  bg: string;
  art: string;
}

/**
 * 角色呈现顺序，全站唯一一份。
 *
 * 创建向导的右栏、添加角色对话框、规则页左栏都按它排序；留在某个页面里会变成
 * 三份各自维护的副本。狼人阵营在前，然后是神职、平民。
 */
export const ROLE_ORDER: readonly string[] = [
  'wolf-killer-werewolf',
  'wolf-killer-werewolf-king',
  'wolf-killer-wolf-king',
  'wolf-killer-evil-knight',
  'wolf-killer-gargoyle',
  'wolf-killer-wolf-beauty',
  'wolf-killer-seer',
  'wolf-killer-witch',
  'wolf-killer-hunter',
  'wolf-killer-guard',
  'wolf-killer-idiot',
  'wolf-killer-dream-weaver',
  'wolf-killer-gravekeeper',
  'wolf-killer-knight',
  'wolf-killer-old-drunkard',
  'wolf-killer-villager',
];

const ROLE_ROWS: readonly { id: string; key: RoleColorKey; label: string }[] = [
  { id: 'wolf-killer-werewolf', key: 'werewolf', label: '狼人' },
  { id: 'wolf-killer-witch', key: 'witch', label: '女巫' },
  { id: 'wolf-killer-seer', key: 'seer', label: '预言家' },
  { id: 'wolf-killer-hunter', key: 'hunter', label: '猎人' },
  { id: 'wolf-killer-villager', key: 'villager', label: '村民' },
  { id: 'wolf-killer-guard', key: 'guard', label: '守卫' },
  { id: 'wolf-killer-idiot', key: 'idiot', label: '白痴' },
  { id: 'wolf-killer-werewolf-king', key: 'werewolf_king', label: '白狼王' },
  // 狼王与白狼王共用狼王配色（同一「狼王」视觉家族），但立绘各走各的 id ——
  // 狼王还没有自己的立绘，所以显示卡背。两个 id 只差词序，任何地方都不许简写。
  { id: 'wolf-killer-wolf-king', key: 'werewolf_king', label: '狼王' },
  { id: 'wolf-killer-evil-knight', key: 'werewolf', label: '恶夜骑士' },
  { id: 'wolf-killer-dream-weaver', key: 'guard', label: '摄梦人' },
  { id: 'wolf-killer-gargoyle', key: 'werewolf', label: '石像鬼' },
  { id: 'wolf-killer-gravekeeper', key: 'guard', label: '守墓人' },
  { id: 'wolf-killer-knight', key: 'knight', label: '骑士' },
  { id: 'wolf-killer-wolf-beauty', key: 'wolf_beauty', label: '狼美人' },
  { id: 'wolf-killer-old-drunkard', key: 'old_drunkard', label: '老酒鬼' },
];

export const ROLE_META: Record<string, RoleMetaEntry> = Object.fromEntries(
  ROLE_ROWS.map(({ id, key, label }) => [id, {
    label,
    color: ROLE_COLORS[key].color,
    bg: ROLE_COLORS[key].bg,
    // 立绘按角色 id 取，没图就是卡背；不再借用配色键对应的那张图。
    art: roleArt(id),
  }]),
);

/** 未登记的角色返回 `undefined`，调用方据此显示「未知身份」。 */
export function roleMetaFor(roleId: string | null | undefined): RoleMetaEntry | undefined {
  if (!roleId) return undefined;
  return ROLE_META[roleId];
}

/** 按 `ROLE_ORDER` 排序；未登记的角色排在最后，组内保持座位序。 */
export function rankOf(roleId: string, order: readonly string[] = ROLE_ORDER): number {
  const index = order.indexOf(roleId);
  return index === -1 ? order.length : index;
}
