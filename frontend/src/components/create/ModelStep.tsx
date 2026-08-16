import { useEffect, useState } from 'react';
import { Box, Button, Radio, Stack, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';

import ModelConfigDialog from '../models/ModelConfigDialog';
import { useModelConfigStore } from '../../store/modelConfigStore';
import type { ModelConfigInput } from '../../store/types';

interface Props {
  totalPlayers: number;
  selectedModelId: string | null;
  onSelect: (id: string | null) => void;
}

export default function ModelStep({ totalPlayers, selectedModelId, onSelect }: Props) {
  const { configs, error, load, create } = useModelConfigStore();
  const [dialogOpen, setDialogOpen] = useState(false);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSaved = async (input: ModelConfigInput) => {
    const saved = await create(input);
    if (saved) {
      setDialogOpen(false);
      onSelect(saved.id);
    }
  };

  return (
    <Box>
      <Typography variant="subtitle2" sx={{ mb: 1 }}>
        为整局 {totalPlayers} 位玩家选择模型（一期：整局一个模型）
      </Typography>
      {error && <Typography variant="body2" color="error" sx={{ mb: 1 }}>{error}</Typography>}

      <Stack spacing={1}>
        <Box
          onClick={() => onSelect(null)}
          sx={{
            display: 'flex', alignItems: 'center', gap: 1, cursor: 'pointer',
            bgcolor: 'background.paper', border: '1px solid',
            borderColor: selectedModelId === null ? 'primary.main' : 'divider',
            borderRadius: 2, p: 1.5,
          }}
        >
          <Radio checked={selectedModelId === null} readOnly />
          <Box>
            <Typography variant="body1">环境默认 (.env)</Typography>
            <Typography variant="body2" color="text.secondary">不依赖已存配置</Typography>
          </Box>
        </Box>

        {configs.map((config) => (
          <Box
            key={config.id}
            onClick={() => { if (!config.key_invalid) onSelect(config.id); }}
            sx={{
              display: 'flex', alignItems: 'center', gap: 1,
              cursor: config.key_invalid ? 'not-allowed' : 'pointer',
              opacity: config.key_invalid ? 0.5 : 1,
              bgcolor: 'background.paper', border: '1px solid',
              borderColor: selectedModelId === config.id ? 'primary.main' : 'divider',
              borderRadius: 2, p: 1.5,
            }}
          >
            <Radio checked={selectedModelId === config.id} disabled={config.key_invalid} readOnly />
            <Box>
              <Typography variant="body1">{config.name}</Typography>
              <Typography variant="body2" color="text.secondary">
                {config.model_id} · {config.base_url}
              </Typography>
            </Box>
          </Box>
        ))}

        <Button
          variant="outlined"
          startIcon={<AddIcon />}
          onClick={() => setDialogOpen(true)}
          sx={{ borderStyle: 'dashed', justifyContent: 'flex-start' }}
        >
          当场新建模型配置
        </Button>
      </Stack>

      {dialogOpen && (
        <ModelConfigDialog
          open
          initial={null}
          onClose={() => setDialogOpen(false)}
          onSave={handleSaved}
        />
      )}
    </Box>
  );
}
