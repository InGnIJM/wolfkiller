import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Alert, Box, Button, CircularProgress, Container, FormControl, FormControlLabel,
  InputLabel, MenuItem, Select, Snackbar, Stack, Switch, Typography,
} from '@mui/material';
import type { SelectChangeEvent } from '@mui/material';

import { useAppSettingsStore } from '../../store/appSettingsStore';
import {
  useUiSettingsStore,
} from '../../store/uiSettingsStore';
import type { LanguagePreference, ThemePreference } from '../../store/uiSettingsStore';
import { BACKDROP } from '../../theme/tokens';

interface CaptureNote {
  lead: string;
  body: string;
}

export default function SettingsPage() {
  const { t } = useTranslation();
  const {
    setting, autoReviewSetting, loading, error, load, setEnabled, setAutoReviewEnabled,
  } = useAppSettingsStore();
  const language = useUiSettingsStore((s) => s.language);
  const themeMode = useUiSettingsStore((s) => s.themeMode);
  const setLanguage = useUiSettingsStore((s) => s.setLanguage);
  const setThemeMode = useUiSettingsStore((s) => s.setThemeMode);
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    void load();
  }, [load]);

  const closeFeedback = () => setFeedback(null);

  const handleLanguageChange = (event: SelectChangeEvent) => {
    setLanguage(event.target.value as LanguagePreference);
  };

  const handleThemeChange = (event: SelectChangeEvent) => {
    setThemeMode(event.target.value as ThemePreference);
  };

  const handleToggle = async (enabled: boolean) => {
    const next = await setEnabled(enabled);
    if (!next) return;
    setFeedback(t(enabled
      ? 'settings.promptCapture.feedbackEnabled'
      : 'settings.promptCapture.feedbackDisabled'));
  };

  const handleFollowEnv = async () => {
    const next = await setEnabled(null);
    if (!next) return;
    setFeedback(t('settings.promptCapture.feedbackFollowEnv', {
      value: t(next.enabled ? 'common.enabled' : 'common.disabled'),
    }));
  };

  const handleAutoReviewToggle = async (enabled: boolean) => {
    const next = await setAutoReviewEnabled(enabled);
    if (!next) return;
    setFeedback(t(enabled
      ? 'settings.autoReview.feedbackEnabled'
      : 'settings.autoReview.feedbackDisabled'));
  };

  const captureNotes = t('settings.promptCapture.notes', {
    returnObjects: true,
  }) as CaptureNote[];

  return (
    <Container maxWidth="md" sx={{
      position: 'relative', zIndex: 1, py: 4,
      bgcolor: BACKDROP.textPlate, backdropFilter: 'blur(12px)', borderRadius: 2,
    }}>
      <Typography variant="h4" sx={{ fontWeight: 400, mb: 3 }}>{t('settings.title')}</Typography>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      <Box sx={{
        mb: 3,
        bgcolor: 'background.paper',
        border: '1px solid', borderColor: 'divider',
        borderRadius: 2, p: 2.5,
      }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 500 }}>
          {t('settings.appearance')}
        </Typography>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2.5} sx={{ mt: 2 }}>
          <FormControl size="small" sx={{ minWidth: 200 }}>
            <InputLabel id="language-select-label">{t('settings.language')}</InputLabel>
            <Select
              labelId="language-select-label"
              label={t('settings.language')}
              value={language}
              onChange={handleLanguageChange}
            >
              <MenuItem value="system">{t('settings.languageFollowSystem')}</MenuItem>
              <MenuItem value="zh-CN">简体中文</MenuItem>
              <MenuItem value="en">English</MenuItem>
            </Select>
          </FormControl>
          <FormControl size="small" sx={{ minWidth: 200 }}>
            <InputLabel id="theme-select-label">{t('settings.themeMode')}</InputLabel>
            <Select
              labelId="theme-select-label"
              label={t('settings.themeMode')}
              value={themeMode}
              onChange={handleThemeChange}
            >
              <MenuItem value="system">{t('settings.themeFollowSystem')}</MenuItem>
              <MenuItem value="dark">{t('settings.themeDark')}</MenuItem>
              <MenuItem value="light">{t('settings.themeLight')}</MenuItem>
            </Select>
          </FormControl>
        </Stack>
      </Box>

      {loading ? (
        <Box sx={{ display: 'grid', placeItems: 'center', py: 6 }}>
          <CircularProgress size={28} aria-label={t('settings.loadingAria')} />
        </Box>
      ) : setting && (
        <Box sx={{
          bgcolor: 'background.paper',
          border: '1px solid', borderColor: 'divider',
          borderRadius: 2, p: 2.5,
        }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 500 }}>
            {t('settings.promptCapture.title')}
          </Typography>

          <FormControlLabel
            sx={{ mt: 1.5, ml: 0 }}
            control={(
              <Switch
                checked={setting.enabled}
                onChange={(event) => { void handleToggle(event.target.checked); }}
              />
            )}
            label={t('settings.promptCapture.switchLabel')}
          />
          <Typography variant="body2" color="text.secondary">
            {t('settings.promptCapture.status', {
              value: t(setting.enabled ? 'common.enabled' : 'common.disabled'),
            })}
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {t(`settings.promptCapture.source.${setting.source}`)}
          </Typography>

          <Box sx={{ mt: 2.5 }}>
            <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 1 }}>
              {t('settings.promptCapture.notesTitle')}
            </Typography>
            <Stack spacing={1.2}>
              {captureNotes.map((note, index) => (
                <Typography key={note.lead} variant="body2" component="p" sx={{ lineHeight: 1.75 }}>
                  <Box component="span" sx={{ fontWeight: 600 }}>{`${index + 1}. ${note.lead}`}</Box>
                  {' — '}
                  {note.body}
                </Typography>
              ))}
            </Stack>
          </Box>

          {setting.source === 'ui' && (
            <Button
              variant="outlined"
              size="small"
              sx={{ mt: 2.5 }}
              onClick={() => { void handleFollowEnv(); }}
            >
              {t('settings.promptCapture.revertToEnv')}
            </Button>
          )}
        </Box>
      )}

      {!loading && autoReviewSetting && (
        <Box sx={{
          mt: 3,
          bgcolor: 'background.paper',
          border: '1px solid', borderColor: 'divider',
          borderRadius: 2, p: 2.5,
        }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 500 }}>
            {t('settings.autoReview.title')}
          </Typography>
          <FormControlLabel
            sx={{ mt: 1.5, ml: 0 }}
            control={(
              <Switch
                checked={autoReviewSetting.enabled}
                onChange={(event) => { void handleAutoReviewToggle(event.target.checked); }}
              />
            )}
            label={t('settings.autoReview.switchLabel')}
          />
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {t('settings.autoReview.description')}
          </Typography>
        </Box>
      )}

      <Snackbar
        open={feedback !== null}
        autoHideDuration={6000}
        onClose={closeFeedback}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity="success" variant="filled" onClose={closeFeedback} sx={{ width: '100%' }}>
          {feedback}
        </Alert>
      </Snackbar>
    </Container>
  );
}
