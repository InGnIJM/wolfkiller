/** Readable labels for `recovery_block_code`, with the raw code as the fallback.
 *
 * The backend code is the stable contract (`recovery_blocked` games are keyed by
 * it); the label is only for the lobby tooltip, so an unknown code — a newer
 * backend, or an archive written before a rename — must still show something.
 */
const RECOVERY_BLOCK_LABELS: Record<string, string> = {
  checkpoint_missing: '找不到检查点',
  checkpoint_corrupt: '检查点损坏',
  checkpoint_version_unsupported: '检查点版本不受支持',
  registry_incompatible: '角色或契约已不存在',
  contract_incompatible: '契约版本不兼容',
  role_declaration_drift: '角色声明已变更',
  contract_digest_drift: '契约内容已变更',
  registry_identity_unknown: '缺少角色注册表身份',
  prompt_drift: '提示词已更新',
  model_drift: '模型参数已变更',
  model_config_missing: '模型配置缺失',
  model_key_unavailable: '模型密钥不可用',
  registry_mismatch: '注册表不一致（旧代码）',
  legacy_archive: '旧档，没有耐久检查点',
};

export function recoveryBlockLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  return RECOVERY_BLOCK_LABELS[code] ?? code;
}
