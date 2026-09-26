import { describe, expect, it } from 'vitest';

import { roleMetaFor } from '../roleMeta';
import { CARD_BACK, ROLE_ART, ROLE_COLORS } from '../../../theme/tokens';
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
  ['wolf-killer-knight', 'knight', '骑士'],
  ['wolf-killer-wolf-beauty', 'wolf_beauty', '狼美人'],
  ['wolf-killer-old-drunkard', 'old_drunkard', '老酒鬼'],
];

describe('roleMeta', () => {
  it('maps every seated role id to its label, colors, and portrait', () => {
    for (const [roleId, key, label] of ROLES) {
      expect(roleMetaFor(roleId)).toEqual({
        label,
        color: ROLE_COLORS[key].color,
        bg: ROLE_COLORS[key].bg,
        art: ROLE_ART[key],
      });
    }
  });

  it('returns undefined for a hidden or unknown identity', () => {
    expect(roleMetaFor(undefined)).toBeUndefined();
    expect(roleMetaFor(null)).toBeUndefined();
    expect(roleMetaFor('')).toBeUndefined();
    expect(roleMetaFor('wolf-killer-unknown')).toBeUndefined();
    expect(roleMetaFor('wolf-killer-werewolf')?.art).not.toBe(CARD_BACK);
  });
});
