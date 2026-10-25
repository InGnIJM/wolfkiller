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

const ROLE_ROWS: readonly { id: string; key: RoleColorKey; label: string }[] = [
  { id: 'wolf-killer-werewolf', key: 'werewolf', label: '狼人' },
  { id: 'wolf-killer-witch', key: 'witch', label: '女巫' },
  { id: 'wolf-killer-awakened-witch', key: 'witch', label: '觉醒女巫' },
  { id: 'wolf-killer-seer', key: 'seer', label: '预言家' },
  { id: 'wolf-killer-hunter', key: 'hunter', label: '猎人' },
  { id: 'wolf-killer-awakened-hunter', key: 'hunter', label: '觉醒猎人' },
  { id: 'wolf-killer-villager', key: 'villager', label: '平民' },
  { id: 'wolf-killer-guard', key: 'guard', label: '守卫' },
  { id: 'wolf-killer-idiot', key: 'idiot', label: '白痴' },
  { id: 'wolf-killer-werewolf-king', key: 'werewolf_king', label: '白狼王' },
  // 狼王与白狼王共用狼王配色（同一「狼王」视觉家族），但立绘各走各的 id ——
  // 两个 id 只差词序，任何地方都不许简写。
  { id: 'wolf-killer-wolf-king', key: 'werewolf_king', label: '狼王' },
  { id: 'wolf-killer-awakened-wolf-king', key: 'werewolf_king', label: '觉醒狼王' },
  { id: 'wolf-killer-evil-knight', key: 'werewolf', label: '恶夜骑士' },
  { id: 'wolf-killer-dream-weaver', key: 'guard', label: '摄梦人' },
  { id: 'wolf-killer-gargoyle', key: 'werewolf', label: '石像鬼' },
  { id: 'wolf-killer-gravekeeper', key: 'guard', label: '守墓人' },
  { id: 'wolf-killer-knight', key: 'knight', label: '骑士' },
  { id: 'wolf-killer-wolf-beauty', key: 'wolf_beauty', label: '狼美人' },
  { id: 'wolf-killer-awakened-wolf-beauty', key: 'wolf_beauty', label: '觉醒狼美人' },
  { id: 'wolf-killer-old-drunkard', key: 'old_drunkard', label: '老酒鬼' },
  { id: 'wolf-killer-lonely-girl', key: 'villager', label: '孤独少女' },
  { id: 'wolf-killer-awakened-seer', key: 'seer', label: '觉醒预言家' },
  { id: 'wolf-killer-fragrance-phantom', key: 'werewolf', label: '寻香魅影' },
  { id: 'wolf-killer-pure-white', key: 'seer', label: '纯白之女' },
  { id: 'wolf-killer-wolf-witch', key: 'werewolf', label: '狼巫' },
  { id: 'wolf-killer-crimson-envoy', key: 'werewolf', label: '赤月使徒' },
  { id: 'wolf-killer-nightmare', key: 'werewolf', label: '噩梦之影' },
  { id: 'wolf-killer-mask', key: 'werewolf', label: '假面' },
  { id: 'wolf-killer-demon-hunter', key: 'hunter', label: '猎魔人' },
  { id: 'wolf-killer-dancer', key: 'guard', label: '舞者' },
  { id: 'wolf-killer-miracle-merchant', key: 'hunter', label: '奇迹商人' },
  { id: 'wolf-killer-mirror-girl', key: 'seer', label: '魔镜少女' },
  { id: 'wolf-killer-awakened-hidden-wolf', key: 'werewolf', label: '觉醒隐狼' },
  { id: 'wolf-killer-radiance-earl', key: 'guard', label: '流光伯爵' },
  { id: 'wolf-killer-eclipse-maid', key: 'werewolf', label: '蚀日侍女' },
  { id: 'wolf-killer-time-wolf', key: 'werewolf', label: '蚀时狼妃' },
  { id: 'wolf-killer-order-prince', key: 'knight', label: '定序王子' },
  { id: 'wolf-killer-fool-seer', key: 'seer', label: '愚言家' },
  { id: 'wolf-killer-iron-villager', key: 'villager', label: '铁好人' },
  { id: 'wolf-killer-crow', key: 'villager', label: '乌鸦' },
  { id: 'wolf-killer-cupid', key: 'guard', label: '丘比特' },
  { id: 'wolf-killer-admirer', key: 'villager', label: '暗恋者' },
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

/** 上帝视角按座位读快照上的真实身份短名；座位或角色未知时不猜。 */
export function roleLabelForSeat(
  seat: number | null | undefined,
  players: Record<number, { role?: string }> | undefined,
): string | undefined {
  if (seat == null || players == null) return undefined;
  return roleMetaFor(players[seat]?.role)?.label;
}

/** 按 `ROLE_ORDER` 排序；未登记的角色排在最后，组内保持座位序。 */
export function rankOf(roleId: string, order: readonly string[] = ROLE_ORDER): number {
  const index = order.indexOf(roleId);
  return index === -1 ? order.length : index;
}
