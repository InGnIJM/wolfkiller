import { describe, expect, it } from 'vitest';

import { rankOf, roleMetaFor, ROLE_META, ROLE_ORDER } from '../roleMeta';
import { CARD_BACK, ROLE_ART, ROLE_ART_BY_ID, ROLE_COLORS, roleArt } from '../../../theme/tokens';
import type { RoleColorKey } from '../../../theme/tokens';

const ROLES: readonly [string, RoleColorKey, string][] = [
  ['wolf-killer-werewolf', 'werewolf', '狼人'],
  ['wolf-killer-witch', 'witch', '女巫'],
  ['wolf-killer-seer', 'seer', '预言家'],
  ['wolf-killer-hunter', 'hunter', '猎人'],
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
  ['wolf-killer-old-drunkard', 'old_drunkard', '老酒鬼'],
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

  it('does not hand a role a portrait it does not have', () => {
    // 狼王与白狼王共用配色，但立绘必须各走各的。
    expect(roleMetaFor('wolf-killer-werewolf-king')?.art).toBe(
      ROLE_ART_BY_ID['wolf-killer-werewolf-king'],
    );
    expect(roleMetaFor('wolf-killer-wolf-king')?.art).toBe(CARD_BACK);
    expect(roleMetaFor('wolf-killer-evil-knight')?.art).toBe(CARD_BACK);
    expect(roleMetaFor('wolf-killer-gargoyle')?.art).toBe(CARD_BACK);
    // 配色键仍然映射到那 11 张图，没有因为这次改造而失效。
    expect(ROLE_ART.werewolf_king).toEqual(expect.any(String));
  });

  it('returns undefined for a hidden or unknown identity', () => {
    expect(roleMetaFor(undefined)).toBeUndefined();
    expect(roleMetaFor(null)).toBeUndefined();
    expect(roleMetaFor('')).toBeUndefined();
    expect(roleMetaFor('wolf-killer-unknown')).toBeUndefined();
    expect(roleMetaFor('wolf-killer-werewolf')?.art).not.toBe(CARD_BACK);
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
