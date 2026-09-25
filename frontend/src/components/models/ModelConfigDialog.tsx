import { useState } from 'react';
import {
  Button, Dialog, DialogActions, DialogContent, DialogTitle,
  IconButton, Stack, TextField, Typography,
} from '@mui/material';
import DeleteOutlinedIcon from '@mui/icons-material/DeleteOutlined';

import { testModelConnection } from '../../api/client';
import type {
  ModelConfig, ModelConfigInput, ModelTestResult, ProviderProfileId,
} from '../../store/types';
import { PROVIDER_PROFILE_OPTIONS, isProviderProfileId } from './providerProfiles';

/** RFC 7230 token charset, mirrors the backend header-name rule. */
const HEADER_NAME_PATTERN = /^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$/;
/** Managed by the SDK or gateway; mirrors the backend reserved list. */
const RESERVED_HEADERS = new Set([
  'authorization', 'content-type', 'content-length', 'host', 'connection',
  'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'proxy-connection',
  'te', 'trailer', 'transfer-encoding', 'upgrade', 'accept-encoding',
]);
const MAX_HEADER_ROWS = 32;

interface HeaderRow {
  key: string;
  value: string;
}

/** Deterministic row order: sorted by key name, case-insensitively. */
function headerRowsFrom(headers: Record<string, string> | undefined): HeaderRow[] {
  const entries = Object.entries(headers ?? {});
  entries.sort(([a], [b]) => {
    const left = a.toLowerCase();
    const right = b.toLowerCase();
    if (left === right) return a < b ? -1 : a > b ? 1 : 0;
    return left < right ? -1 : 1;
  });
  return entries.map(([key, value]) => ({ key, value }));
}

function headerRowError(row: HeaderRow, duplicate: boolean): string {
  const key = row.key.trim();
  if (!key) return '名称必填';
  if (!HEADER_NAME_PATTERN.test(key)) return '名称含非法字符';
  if (RESERVED_HEADERS.has(key.toLowerCase())) return '该请求头由系统管理，不能自定义';
  if (/[\r\n\0]/.test(row.value)) return '值不能包含换行';
  if (duplicate) return '名称重复';
  return '';
}

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
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>(() => headerRowsFrom(initial?.headers));
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
  const duplicateKeys = new Set(
    headerRows
      .map((row) => row.key.trim().toLowerCase())
      .filter((key, index, keys) => Boolean(key) && keys.indexOf(key) !== index),
  );
  const headerErrors = headerRows.map((row) =>
    headerRowError(row, duplicateKeys.has(row.key.trim().toLowerCase())),
  );
  const headersError = headerErrors.some(Boolean);
  const tooManyHeaders = headerRows.length > MAX_HEADER_ROWS;
  const hasErrors = Boolean(
    nameError || urlError || modelError || headersError || tooManyHeaders,
  );
  const tested = testResult?.ok === true;
  const canSave = !hasErrors && tested && !saving;
  const baseUrlPlaceholder = PROVIDER_PROFILE_OPTIONS
    .find((option) => option.id === providerProfile)?.baseUrlPlaceholder ?? '';
  const isAnthropic = providerProfile === 'anthropic' || providerProfile === 'custom-anthropic';

  /** Non-empty rows only, keys and values trimmed. */
  const headersPayload = (): Record<string, string> => {
    const headers: Record<string, string> = {};
    headerRows.forEach((row) => {
      const key = row.key.trim();
      if (key) headers[key] = row.value.trim();
    });
    return headers;
  };

  const invalidateTest = () => {
    setTestResult((prev) => (prev?.ok ? null : prev));
  };

  const updateHeaderRow = (index: number, patch: Partial<HeaderRow>) => {
    setHeaderRows((rows) =>
      rows.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
    invalidateTest();
  };

  const addHeaderRow = () => {
    setHeaderRows((rows) => [...rows, { key: '', value: '' }]);
    invalidateTest();
  };

  const removeHeaderRow = (index: number) => {
    setHeaderRows((rows) => rows.filter((_, i) => i !== index));
    invalidateTest();
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
      headers: headersPayload(),
    });
    setSaving(false);
  };

  const handleTest = async () => {
    setTouched(true);
    if (hasErrors) return;
    setTesting(true);
    try {
      // Saving is gated on a passing test, so the test must carry the current
      // form value of every editable field. Sending only ``config_id`` would
      // test the stored config and unlock saving for an untested edit.
      const values = {
        base_url: baseUrl.trim(),
        model_id: modelId.trim(),
        strict_base_url: strictUrl.trim(),
        api_key: apiKey,
        provider_profile: providerProfile,
        headers: headersPayload(),
      };
      const result = initial
        ? await testModelConnection({ config_id: initial.id, ...values })
        : await testModelConnection(values);
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
              <Stack spacing={1}>
                <Typography variant="body2" color="text.secondary">
                  自定义请求头（可选）
                </Typography>
                {headerRows.map((row, index) => (
                  <Stack
                    key={index}
                    direction="row"
                    spacing={1}
                    sx={{ alignItems: 'flex-start' }}
                  >
                    <TextField
                      label="名称"
                      value={row.key}
                      onChange={(e) => updateHeaderRow(index, { key: e.target.value })}
                      onBlur={() => setTouched(true)}
                      error={touched && Boolean(headerErrors[index])}
                      helperText={touched ? headerErrors[index] : ''}
                      size="small"
                      fullWidth
                      slotProps={{ htmlInput: { 'aria-label': '请求头名称' } }}
                    />
                    <TextField
                      label="值"
                      value={row.value}
                      onChange={(e) => updateHeaderRow(index, { value: e.target.value })}
                      size="small"
                      fullWidth
                      slotProps={{ htmlInput: { 'aria-label': '请求头值' } }}
                    />
                    <IconButton
                      aria-label={`删除请求头 ${index + 1}`}
                      onClick={() => removeHeaderRow(index)}
                      size="small"
                      sx={{ mt: 0.5, color: 'text.secondary' }}
                    >
                      <DeleteOutlinedIcon fontSize="small" />
                    </IconButton>
                  </Stack>
                ))}
                <Button
                  color="inherit"
                  onClick={addHeaderRow}
                  size="small"
                  sx={{ alignSelf: 'flex-start' }}
                >
                  添加请求头
                </Button>
                {tooManyHeaders && (
                  <Typography variant="body2" color="error.main">
                    最多 {MAX_HEADER_ROWS} 个请求头
                  </Typography>
                )}
              </Stack>
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
