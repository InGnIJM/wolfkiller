import { useState } from 'react';
import {
  Button, Dialog, DialogActions, DialogContent, DialogTitle,
  Stack, TextField, Typography,
} from '@mui/material';

import { testModelConnection } from '../../api/client';
import type {
  ModelConfig, ModelConfigInput, ModelTestResult, ProviderProfileId,
} from '../../store/types';
import { PROVIDER_PROFILE_OPTIONS, isProviderProfileId } from './providerProfiles';

interface Props {
  open: boolean;
  initial: ModelConfig | null;
  onClose: () => void;
  onSave: (input: ModelConfigInput) => Promise<void>;
}

export default function ModelConfigDialog({ open, initial, onClose, onSave }: Props) {
  const [name, setName] = useState(initial?.name ?? '');
  const [baseUrl, setBaseUrl] = useState(initial?.base_url ?? '');
  const [modelId, setModelId] = useState(initial?.model_id ?? '');
  const [apiKey, setApiKey] = useState('');
  const [providerProfile, setProviderProfile] = useState<ProviderProfileId>(
    initial?.provider_profile ?? 'auto',
  );
  const [temperature, setTemperature] = useState(
    initial?.temperature != null ? String(initial.temperature) : '',
  );
  const [strictUrl, setStrictUrl] = useState(initial?.strict_base_url ?? '');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [testResult, setTestResult] = useState<ModelTestResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [touched, setTouched] = useState(false);

  const nameError = !name.trim() ? '名称必填' : '';
  const urlError = !/^https?:\/\//.test(baseUrl.trim())
    ? '必须以 http:// 或 https:// 开头'
    : '';
  const modelError = !modelId.trim() ? '模型 ID 必填' : '';
  const hasErrors = Boolean(nameError || urlError || modelError);
  const tested = testResult?.ok === true;
  const canSave = !hasErrors && tested && !saving;
  const baseUrlPlaceholder = PROVIDER_PROFILE_OPTIONS
    .find((option) => option.id === providerProfile)?.baseUrlPlaceholder ?? '';
  const isAnthropic = providerProfile === 'anthropic' || providerProfile === 'custom-anthropic';

  const invalidateTest = () => {
    setTestResult((prev) => (prev?.ok ? null : prev));
  };

  const handleSave = async () => {
    setTouched(true);
    if (hasErrors || !tested) return;
    setSaving(true);
    await onSave({
      name: name.trim(),
      base_url: baseUrl.trim(),
      model_id: modelId.trim(),
      api_key: apiKey,
      temperature: temperature.trim() ? Number(temperature) : null,
      strict_base_url: strictUrl.trim() || null,
      provider_profile: providerProfile,
    });
    setSaving(false);
  };

  const handleTest = async () => {
    setTouched(true);
    if (hasErrors) return;
    setTesting(true);
    try {
      const result = initial
        ? await testModelConnection({
            config_id: initial.id, api_key: apiKey, provider_profile: providerProfile,
          })
        : await testModelConnection({
            base_url: baseUrl.trim(), api_key: apiKey, model_id: modelId.trim(),
            provider_profile: providerProfile,
          });
      setTestResult(result);
    } catch (error) {
      setTestResult({
        ok: false, latency_ms: null,
        error: error instanceof Error ? error.message : String(error),
      });
    }
    setTesting(false);
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{initial ? '编辑模型配置' : '新建模型配置'}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => setTouched(true)}
            error={touched && Boolean(nameError)}
            helperText={touched ? nameError : ''}
            size="small"
            fullWidth
          />
          <TextField
            select
            label="接口协议"
            value={providerProfile}
            onChange={(e) => {
              const next = e.target.value;
              if (isProviderProfileId(next)) {
                setProviderProfile(next);
                invalidateTest();
              }
            }}
            slotProps={{ select: { native: true } }}
            helperText={
              isAnthropic
                ? 'Anthropic Messages API：Base URL 不含 /v1；temperature 会被截断到 0~1'
                : '自动识别仅支持官方域名；中转站请手动选择协议'
            }
            size="small"
            fullWidth
          >
            {PROVIDER_PROFILE_OPTIONS.map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </TextField>
          <TextField
            label="Base URL"
            placeholder={baseUrlPlaceholder}
            value={baseUrl}
            onChange={(e) => {
              setBaseUrl(e.target.value);
              invalidateTest();
            }}
            onBlur={() => setTouched(true)}
            error={touched && Boolean(urlError)}
            helperText={touched ? urlError : ''}
            size="small"
            fullWidth
          />
          <TextField
            label="模型 ID"
            placeholder={isAnthropic ? 'claude-sonnet-4-5' : 'deepseek-v4-flash'}
            value={modelId}
            onChange={(e) => {
              setModelId(e.target.value);
              invalidateTest();
            }}
            onBlur={() => setTouched(true)}
            error={touched && Boolean(modelError)}
            helperText={touched ? modelError : ''}
            size="small"
            fullWidth
          />
          <TextField
            label={initial?.has_key ? 'API Key（留空 = 保留原 key）' : 'API Key'}
            type="password"
            value={apiKey}
            onChange={(e) => {
              setApiKey(e.target.value);
              invalidateTest();
            }}
            size="small"
            fullWidth
          />
          <Typography
            component="button"
            onClick={() => setShowAdvanced((v) => !v)}
            sx={{ alignSelf: 'flex-start', cursor: 'pointer', bgcolor: 'transparent', border: 'none', color: 'primary.main', p: 0 }}
          >
            {showAdvanced ? '▾ 高级选项' : '▸ 高级选项（可选）'}
          </Typography>
          {showAdvanced && (
            <>
              <TextField
                label="Temperature（可选，0~2）"
                type="number"
                value={temperature}
                onChange={(e) => setTemperature(e.target.value)}
                slotProps={{ htmlInput: { min: 0, max: 2, step: 0.1 } }}
                size="small"
                fullWidth
              />
              <TextField
                label="严格模式地址（可选，留空按厂商自动推导）"
                value={strictUrl}
                onChange={(e) => {
                  setStrictUrl(e.target.value);
                  invalidateTest();
                }}
                size="small"
                fullWidth
              />
            </>
          )}
          {testResult && (
            <Typography
              variant="body2"
              color={testResult.ok ? 'success.main' : 'error.main'}
            >
              {testResult.ok
                ? `连接成功（${testResult.latency_ms}ms）`
                : `连接失败：${testResult.error}`}
            </Typography>
          )}
          {!tested && !hasErrors && (
            <Typography variant="body2" color="text.secondary">
              保存前需通过连接测试
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button
          color="inherit"
          onClick={() => void handleTest()}
          disabled={testing || hasErrors}
        >
          测试连接
        </Button>
        <Button color="inherit" onClick={onClose}>取消</Button>
        <Button
          variant="contained"
          disableElevation
          onClick={() => void handleSave()}
          disabled={!canSave}
        >
          保存
        </Button>
      </DialogActions>
    </Dialog>
  );
}
