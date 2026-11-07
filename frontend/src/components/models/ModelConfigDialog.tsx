import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
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

function headerRowError(row: HeaderRow, duplicate: boolean, t: TFunction): string {
  const key = row.key.trim();
  if (!key) return t('models.nameRequired');
  if (!HEADER_NAME_PATTERN.test(key)) return t('models.headerNameInvalid');
  if (RESERVED_HEADERS.has(key.toLowerCase())) return t('models.headerReserved');
  if (/[\r\n\0]/.test(row.value)) return t('models.headerValueNewline');
  if (duplicate) return t('models.headerDuplicate');
  return '';
}

interface Props {
  open: boolean;
  initial: ModelConfig | null;
  onClose: () => void;
  onSave: (input: ModelConfigInput) => Promise<void>;
}

export default function ModelConfigDialog({ open, initial, onClose, onSave }: Props) {
  const { t } = useTranslation();
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
  const [reasoningEffort, setReasoningEffort] = useState(
    initial?.reasoning_effort ?? '',
  );
  const [strictUrl, setStrictUrl] = useState(initial?.strict_base_url ?? '');
  const [headerRows, setHeaderRows] = useState<HeaderRow[]>(() => headerRowsFrom(initial?.headers));
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [testResult, setTestResult] = useState<ModelTestResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [touched, setTouched] = useState(false);

  const nameError = !name.trim() ? t('models.nameRequired') : '';
  const urlError = !/^https?:\/\//.test(baseUrl.trim())
    ? t('models.baseUrlScheme')
    : '';
  const modelError = !modelId.trim() ? t('models.modelIdRequired') : '';
  const duplicateKeys = new Set(
    headerRows
      .map((row) => row.key.trim().toLowerCase())
      .filter((key, index, keys) => Boolean(key) && keys.indexOf(key) !== index),
  );
  const headerErrors = headerRows.map((row) =>
    headerRowError(row, duplicateKeys.has(row.key.trim().toLowerCase()), t),
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
      reasoning_effort: reasoningEffort || null,
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
        reasoning_effort: reasoningEffort,
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
      <DialogTitle>{t(initial ? 'models.editTitle' : 'models.createTitle')}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label={t('models.nameLabel')}
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
            label={t('models.protocolLabel')}
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
                ? t('models.anthropicHint')
                : t('models.autoHint')
            }
            size="small"
            fullWidth
          >
            {PROVIDER_PROFILE_OPTIONS.map((option) => (
              <option key={option.id} value={option.id}>{t(option.labelKey)}</option>
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
            label={t('models.modelIdLabel')}
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
            label={t(initial?.has_key ? 'models.apiKeyKeep' : 'models.apiKey')}
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
            {t(showAdvanced ? 'models.advancedShow' : 'models.advancedHide')}
          </Typography>
          {showAdvanced && (
            <>
              <TextField
                label={t('models.temperatureLabel')}
                type="number"
                value={temperature}
                onChange={(e) => setTemperature(e.target.value)}
                slotProps={{ htmlInput: { min: 0, max: 2, step: 0.1 } }}
                size="small"
                fullWidth
              />
              <TextField
                select
                label={t('models.reasoningEffortLabel')}
                value={reasoningEffort}
                onChange={(e) => {
                  setReasoningEffort(e.target.value);
                  invalidateTest();
                }}
                slotProps={{
                  select: { native: true },
                  // The '' option renders visible text, so the label must
                  // stay shrunk or it paints on top of the selection.
                  inputLabel: { shrink: true },
                }}
                helperText={t('models.reasoningEffortHint')}
                size="small"
                fullWidth
              >
                {['', 'none', 'low', 'medium', 'high', 'max'].map((level) => (
                  <option key={level || 'default'} value={level}>
                    {t(`models.reasoningEffortOption.${level || 'default'}`)}
                  </option>
                ))}
              </TextField>
              <TextField
                label={t('models.strictUrlLabel')}
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
                  {t('models.customHeaders')}
                </Typography>
                {headerRows.map((row, index) => (
                  <Stack
                    key={index}
                    direction="row"
                    spacing={1}
                    sx={{ alignItems: 'flex-start' }}
                  >
                    <TextField
                      label={t('models.nameLabel')}
                      value={row.key}
                      onChange={(e) => updateHeaderRow(index, { key: e.target.value })}
                      onBlur={() => setTouched(true)}
                      error={touched && Boolean(headerErrors[index])}
                      helperText={touched ? headerErrors[index] : ''}
                      size="small"
                      fullWidth
                      slotProps={{ htmlInput: { 'aria-label': t('models.headerNameAria') } }}
                    />
                    <TextField
                      label={t('models.headerValue')}
                      value={row.value}
                      onChange={(e) => updateHeaderRow(index, { value: e.target.value })}
                      size="small"
                      fullWidth
                      slotProps={{ htmlInput: { 'aria-label': t('models.headerValueAria') } }}
                    />
                    <IconButton
                      aria-label={t('models.deleteHeader', { index: index + 1 })}
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
                  {t('models.addHeader')}
                </Button>
                {tooManyHeaders && (
                  <Typography variant="body2" color="error.main">
                    {t('models.maxHeaders', { max: MAX_HEADER_ROWS })}
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
                ? t('models.testOk', { ms: testResult.latency_ms })
                : t('models.testFail', { error: testResult.error })}
            </Typography>
          )}
          {!tested && !hasErrors && (
            <Typography variant="body2" color="text.secondary">
              {t('models.testBeforeSave')}
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
          {t('models.testConnection')}
        </Button>
        <Button color="inherit" onClick={onClose}>{t('common.cancel')}</Button>
        <Button
          variant="contained"
          disableElevation
          onClick={() => void handleSave()}
          disabled={!canSave}
        >
          {t('models.save')}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
