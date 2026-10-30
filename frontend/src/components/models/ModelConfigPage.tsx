import { useTranslation } from 'react-i18next';
import { useEffect, useState } from 'react';
import { Alert, Box, Button, Container, Stack, Typography } from '@mui/material';

import ModelConfigDialog from './ModelConfigDialog';
import { providerProfileLabel } from './providerProfiles';
import { useModelConfigStore } from '../../store/modelConfigStore';
import { testModelConnection } from '../../api/client';
import type { ModelConfig, ModelConfigInput, ModelTestResult } from '../../store/types';
import { BACKDROP } from '../../theme/tokens';

export default function ModelConfigPage() {
  const { t } = useTranslation();
  const { configs, loading, error, load, create, update, remove } = useModelConfigStore();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ModelConfig | null>(null);
  const [testResults, setTestResults] = useState<Record<string, ModelTestResult>>({});

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };

  const openEdit = (config: ModelConfig) => {
    setEditing(config);
    setDialogOpen(true);
  };

  const closeDialog = () => {
    setDialogOpen(false);
    setEditing(null);
  };

  const handleSave = async (input: ModelConfigInput) => {
    const saved = editing ? await update(editing.id, input) : await create(input);
    if (saved) closeDialog();
  };

  const handleTest = async (config: ModelConfig) => {
    try {
      const result = await testModelConnection({ config_id: config.id });
      setTestResults((prev) => ({ ...prev, [config.id]: result }));
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [config.id]: {
          ok: false, latency_ms: null,
          error: err instanceof Error ? err.message : String(err),
        },
      }));
    }
  };

  return (
    <Container maxWidth="md" sx={{
      position: 'relative', zIndex: 1, py: 4,
      bgcolor: BACKDROP.textPlate, backdropFilter: 'blur(12px)', borderRadius: 2,
    }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Box>
          <Typography variant="h4" sx={{ fontWeight: 400 }}>{t('models.title')}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {t('models.subtitle')}
          </Typography>
        </Box>
        <Button variant="contained" disableElevation onClick={openCreate}>
          {t('models.create')}
        </Button>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {!loading && configs.length === 0 && (
        <Box sx={{ textAlign: 'center', py: 8 }}>
          <Typography variant="h6" color="text.disabled" sx={{ fontWeight: 400, mb: 1 }}>
            {t('models.empty')}
          </Typography>
          <Typography variant="body2" color="text.disabled">
            {t('models.emptyHint')}
          </Typography>
        </Box>
      )}

      <Stack spacing={1.5}>
        {configs.map((config) => {
          const result = testResults[config.id];
          return (
            <Box
              key={config.id}
              sx={{
                bgcolor: 'background.paper',
                border: '1px solid', borderColor: 'divider',
                borderRadius: 2, p: 2,
              }}
            >
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <Box>
                  <Typography variant="subtitle1" sx={{ fontWeight: 500 }}>
                    {config.name}
                    {config.key_invalid && (
                      <Typography component="span" variant="body2" color="warning.main" sx={{ ml: 1 }}>
                        {t('models.keyInvalidBadge')}
                      </Typography>
                    )}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {config.model_id} · {config.base_url}
                    {config.provider_profile && config.provider_profile !== 'auto'
                      ? ` · ${providerProfileLabel(config.provider_profile, t)}`
                      : ''}
                    {config.temperature != null ? ` · temp ${config.temperature}` : ''}
                    {Object.keys(config.headers ?? {}).length > 0
                      ? ` · ${t('models.customHeadersCount', { count: Object.keys(config.headers).length })}`
                      : ''}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {t('models.apiKeyLabel')}{config.has_key ? config.api_key_masked : t('models.keyNotSet')}
                  </Typography>
                </Box>
                <Stack direction="row" spacing={1}>
                  <Button size="small" color="inherit" onClick={() => void handleTest(config)}>{t('models.test')}</Button>
                  <Button size="small" color="inherit" onClick={() => openEdit(config)}>{t('models.edit')}</Button>
                  <Button size="small" color="inherit" onClick={() => void remove(config.id)}>{t('common.delete')}</Button>
                </Stack>
              </Box>
              {result && (
                result.ok ? (
                  <Alert severity="success" sx={{ mt: 1 }}>{t('models.testOk', { ms: result.latency_ms })}</Alert>
                ) : (
                  <Alert severity="error" sx={{ mt: 1 }}>{t('models.testFail', { error: result.error })}</Alert>
                )
              )}
            </Box>
          );
        })}
      </Stack>

      {dialogOpen && (
        <ModelConfigDialog
          open={dialogOpen}
          initial={editing}
          onClose={closeDialog}
          onSave={handleSave}
        />
      )}
    </Container>
  );
}
