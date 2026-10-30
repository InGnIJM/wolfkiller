import { roleArt, ROLE_COLORS } from '../../theme/tokens';
import type { RoleColorKey } from '../../theme/tokens';

export interface RoleMetaEntry {
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
  'wolf-killer-awakened-wolf-king',
  'wolf-killer-evil-knight',
  'wolf-killer-gargoyle',
  'wolf-killer-wolf-beauty',
  'wolf-killer-awakened-wolf-beauty',
  'wolf-killer-wolf-witch',
  'wolf-killer-crimson-envoy',
  'wolf-killer-nightmare',
  'wolf-killer-mask',
  'wolf-killer-fragrance-phantom',
  'wolf-killer-awakened-hidden-wolf',
  'wolf-killer-eclipse-maid',
  'wolf-killer-time-wolf',
  'wolf-killer-seer',
  'wolf-killer-witch',
  'wolf-killer-awakened-witch',
  'wolf-killer-hunter',
  'wolf-killer-awakened-hunter',
  'wolf-killer-guard',
  'wolf-killer-idiot',
  'wolf-killer-dream-weaver',
  'wolf-killer-gravekeeper',
  'wolf-killer-knight',
  'wolf-killer-pure-white',
  'wolf-killer-demon-hunter',
  'wolf-killer-awakened-seer',
  'wolf-killer-dancer',
  'wolf-killer-miracle-merchant',
  'wolf-killer-mirror-girl',
  'wolf-killer-radiance-earl',
  'wolf-killer-order-prince',
  'wolf-killer-fool-seer',
  'wolf-killer-crow',
  // 孤独少女与老酒鬼都是「平民」牌：排在神职之后、普通平民之前。
  'wolf-killer-iron-villager',
  'wolf-killer-admirer',
  'wolf-killer-cupid',
  'wolf-killer-old-drunkard',
  'wolf-killer-lonely-girl',
  'wolf-killer-villager',
];

const ROLE_ROWS: readonly { id: string; key: RoleColorKey }[] = [
  { id: 'wolf-killer-werewolf', key: 'werewolf' },
  { id: 'wolf-killer-witch', key: 'witch' },
  { id: 'wolf-killer-awakened-witch', key: 'witch' },
  { id: 'wolf-killer-seer', key: 'seer' },
  { id: 'wolf-killer-hunter', key: 'hunter' },
  { id: 'wolf-killer-awakened-hunter', key: 'hunter' },
  { id: 'wolf-killer-villager', key: 'villager' },
  { id: 'wolf-killer-guard', key: 'guard' },
  { id: 'wolf-killer-idiot', key: 'idiot' },
  { id: 'wolf-killer-werewolf-king', key: 'werewolf_king' },
  // 狼王与白狼王共用狼王配色（同一「狼王」视觉家族），但立绘各走各的 id ——
  // 两个 id 只差词序，任何地方都不许简写。
  { id: 'wolf-killer-wolf-king', key: 'werewolf_king' },
  { id: 'wolf-killer-awakened-wolf-king', key: 'werewolf_king' },
  { id: 'wolf-killer-evil-knight', key: 'werewolf' },
  { id: 'wolf-killer-dream-weaver', key: 'guard' },
  { id: 'wolf-killer-gargoyle', key: 'werewolf' },
  { id: 'wolf-killer-gravekeeper', key: 'guard' },
  { id: 'wolf-killer-knight', key: 'knight' },
  { id: 'wolf-killer-wolf-beauty', key: 'wolf_beauty' },
  { id: 'wolf-killer-awakened-wolf-beauty', key: 'wolf_beauty' },
  { id: 'wolf-killer-old-drunkard', key: 'old_drunkard' },
  { id: 'wolf-killer-lonely-girl', key: 'villager' },
  { id: 'wolf-killer-awakened-seer', key: 'seer' },
  { id: 'wolf-killer-fragrance-phantom', key: 'werewolf' },
  { id: 'wolf-killer-pure-white', key: 'seer' },
  { id: 'wolf-killer-wolf-witch', key: 'werewolf' },
  { id: 'wolf-killer-crimson-envoy', key: 'werewolf' },
  { id: 'wolf-killer-nightmare', key: 'werewolf' },
  { id: 'wolf-killer-mask', key: 'werewolf' },
  { id: 'wolf-killer-demon-hunter', key: 'hunter' },
  { id: 'wolf-killer-dancer', key: 'guard' },
  { id: 'wolf-killer-miracle-merchant', key: 'hunter' },
  { id: 'wolf-killer-mirror-girl', key: 'seer' },
  { id: 'wolf-killer-awakened-hidden-wolf', key: 'werewolf' },
  { id: 'wolf-killer-radiance-earl', key: 'guard' },
  { id: 'wolf-killer-eclipse-maid', key: 'werewolf' },
  { id: 'wolf-killer-time-wolf', key: 'werewolf' },
  { id: 'wolf-killer-order-prince', key: 'knight' },
  { id: 'wolf-killer-fool-seer', key: 'seer' },
  { id: 'wolf-killer-iron-villager', key: 'villager' },
  { id: 'wolf-killer-crow', key: 'villager' },
  { id: 'wolf-killer-cupid', key: 'guard' },
  { id: 'wolf-killer-admirer', key: 'villager' },
];

/**
 * 角色的视觉元数据（配色 / 底色 / 立绘）。
 *
 * 刻意不含短名：文案统一由 `labels.ts` 的 `ROLE_NAME_LABELS`（zh）与
 * `i18n/labelsEn.ts`（en）管理，展示时经 `useLabels().roleName(id)` 取。
 */
export const ROLE_META: Record<string, RoleMetaEntry> = Object.fromEntries(
  ROLE_ROWS.map(({ id, key }) => [id, {
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
