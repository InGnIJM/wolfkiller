import { describe, expect, it } from 'vitest';

import { rankOf, roleMetaFor, ROLE_META, ROLE_ORDER } from '../roleMeta';
import { CARD_BACK, ROLE_ART_BY_ID, ROLE_COLORS, roleArt } from '../../../theme/tokens';
import type { RoleColorKey } from '../../../theme/tokens';

const ROLES: readonly [string, RoleColorKey, string][] = [
  ['wolf-killer-werewolf', 'werewolf', '狼人'],
  ['wolf-killer-witch', 'witch', '女巫'],
  ['wolf-killer-seer', 'seer', '预言家'],
  ['wolf-killer-hunter', 'hunter', '猎人'],
  ['wolf-killer-awakened-hunter', 'hunter', '觉醒猎人'],
  ['wolf-killer-villager', 'villager', '村民'],
  ['wolf-killer-guard', 'guard', '守卫'],
  ['wolf-killer-idiot', 'idiot', '白痴'],
  ['wolf-killer-werewolf-king', 'werewolf_king', '白狼王'],
  ['wolf-killer-wolf-king', 'werewolf_king', '狼王'],
  ['wolf-killer-evil-knight', 'werewolf', '恶夜骑士'],
  ['wolf-killer-dream-weaver', 'guard', '摄梦人'],
  ['wolf-killer-gargoyle', 'werewolf', '石像鬼'],
  ['wolf-killer-gravekeeper', 'guard', '守墓人'],
  ['wolf-killer-knight', 'knight', '骑士'],
  ['wolf-killer-wolf-beauty', 'wolf_beauty', '狼美人'],
  ['wolf-killer-awakened-wolf-beauty', 'wolf_beauty', '觉醒狼美人'],
  ['wolf-killer-old-drunkard', 'old_drunkard', '老酒鬼'],
  ['wolf-killer-lonely-girl', 'villager', '孤独少女'],
  ['wolf-killer-awakened-seer', 'seer', '觉醒预言家'],
  ['wolf-killer-fragrance-phantom', 'werewolf', '寻香魅影'],
  ['wolf-killer-pure-white', 'seer', '纯白之女'],
  ['wolf-killer-wolf-witch', 'werewolf', '狼巫'],
  ['wolf-killer-crimson-envoy', 'werewolf', '赤月使徒'],
  ['wolf-killer-nightmare', 'werewolf', '噩梦之影'],
  ['wolf-killer-mask', 'werewolf', '假面'],
  ['wolf-killer-dancer', 'guard', '舞者'],
  ['wolf-killer-demon-hunter', 'hunter', '猎魔人'],
  ['wolf-killer-miracle-merchant', 'hunter', '奇迹商人'],
  ['wolf-killer-mirror-girl', 'seer', '魔镜少女'],
  ['wolf-killer-awakened-hidden-wolf', 'werewolf', '觉醒隐狼'],
  ['wolf-killer-radiance-earl', 'guard', '流光伯爵'],
  ['wolf-killer-eclipse-maid', 'werewolf', '蚀日侍女'],
  ['wolf-killer-time-wolf', 'werewolf', '蚀时狼妃'],
  ['wolf-killer-order-prince', 'knight', '定序王子'],
  ['wolf-killer-fool-seer', 'seer', '愚言家'],
  ['wolf-killer-iron-villager', 'villager', '铁好人'],
  ['wolf-killer-crow', 'villager', '乌鸦'],
  ['wolf-killer-cupid', 'guard', '丘比特'],
  ['wolf-killer-admirer', 'villager', '暗恋者'],
];

/** 后端已注册的全部角色；漏一个就会有角色在界面里显示成「未知身份」。 */
const REGISTERED = ROLES.map(([roleId]) => roleId);

describe('roleMeta', () => {
  it('covers every registered role', () => {
    expect(Object.keys(ROLE_META).sort()).toEqual([...REGISTERED].sort());
  });

  it('maps every role id to its label, colors, and its own portrait', () => {
    for (const [roleId, key, label] of ROLES) {
      expect(roleMetaFor(roleId)).toEqual({
        label,
        color: ROLE_COLORS[key].color,
        bg: ROLE_COLORS[key].bg,
        // 立绘按角色 id 取，不再按配色键 —— 共用配色键的角色不再共用一张图。
        art: roleArt(roleId),
      });
    }
  });

  it('hands every registered role its own portrait, never a borrowed one', () => {
    // 曾经这五个角色借图：狼王显示成白狼王、恶夜骑士和石像鬼显示成狼人、
    // 摄梦人和守墓人显示成守卫。现在它们各有各的官方立绘。
    for (const roleId of [
      'wolf-killer-werewolf-king', 'wolf-killer-wolf-king',
      'wolf-killer-evil-knight', 'wolf-killer-gargoyle',
      'wolf-killer-dream-weaver', 'wolf-killer-gravekeeper',
    ]) {
      expect(roleMetaFor(roleId)?.art, roleId).toBe(roleArt(roleId));
    }
    // 狼王与白狼王共用配色，但立绘必须各走各的 —— 文件名不许撞。
    expect(ROLE_ART_BY_ID['wolf-killer-werewolf-king'])
      .not.toBe(ROLE_ART_BY_ID['wolf-killer-wolf-king']);
  });

  it('falls back to the card back for an id it cannot label', () => {
    // 未登记的角色没有 roleMeta，也就没有角色名可查 —— 只能回退卡背。
    expect(roleMetaFor('wolf-killer-not-a-real-role')).toBeUndefined();
    expect(roleArt('wolf-killer-not-a-real-role')).toBe(CARD_BACK);
  });

  it('returns undefined for a hidden or unknown identity', () => {
    expect(roleMetaFor(undefined)).toBeUndefined();
    expect(roleMetaFor(null)).toBeUndefined();
    expect(roleMetaFor('')).toBeUndefined();
    expect(roleMetaFor('wolf-killer-unknown')).toBeUndefined();
    // 已登记且图在仓库里的角色照常拿到自己的图（老酒鬼是自有素材，始终入库）。
    expect(roleMetaFor('wolf-killer-old-drunkard')?.art).not.toBe(CARD_BACK);
  });
});

describe('role labels', () => {
  it('gives every registered role a non-empty Chinese label', () => {
    for (const id of REGISTERED) {
      expect(roleMetaFor(id)?.label, id).toBeTruthy();
    }
    // 记忆面板曾用一张只含 16 个旧角色的表，新增角色会退回显示机器 id。
    expect(roleMetaFor('wolf-killer-crow')?.label).toBe('乌鸦');
  });
});

describe('ROLE_ORDER', () => {
  it('lists every registered role exactly once', () => {
    expect([...ROLE_ORDER].sort()).toEqual([...REGISTERED].sort());
    expect(new Set(ROLE_ORDER).size).toBe(ROLE_ORDER.length);
  });

  it('puts the wolf camp first and the plain villager last', () => {
    expect(ROLE_ORDER[0]).toBe('wolf-killer-werewolf');
    expect(ROLE_ORDER.at(-1)).toBe('wolf-killer-villager');
    // 狼王与白狼王只差词序，两个都必须在表里且位置不同。
    expect(rankOf('wolf-killer-werewolf-king')).toBeLessThan(rankOf('wolf-killer-wolf-king'));
  });

  it('ranks an unknown role last instead of first', () => {
    expect(rankOf('wolf-killer-unknown')).toBe(ROLE_ORDER.length);
    expect(rankOf('wolf-killer-unknown', ['wolf-killer-unknown'])).toBe(0);
  });
});
