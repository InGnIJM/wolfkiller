import { describe, expect, it } from 'vitest';

import { recoveryBlockLabel } from '../recoveryLabels';

describe('recoveryBlockLabel', () => {
  it('maps the codes the backend can store', () => {
    expect(recoveryBlockLabel('checkpoint_missing')).toBe('找不到检查点');
    expect(recoveryBlockLabel('registry_incompatible')).toBe('角色或契约已不存在');
    expect(recoveryBlockLabel('role_declaration_drift')).toBe('角色声明已变更');
    expect(recoveryBlockLabel('registry_mismatch')).toBe('注册表不一致（旧代码）');
  });

  it('falls back to the raw code and reports nothing for an empty one', () => {
    expect(recoveryBlockLabel('some_new_code')).toBe('some_new_code');
    expect(recoveryBlockLabel(null)).toBeNull();
    expect(recoveryBlockLabel(undefined)).toBeNull();
    expect(recoveryBlockLabel('')).toBeNull();
  });
});
