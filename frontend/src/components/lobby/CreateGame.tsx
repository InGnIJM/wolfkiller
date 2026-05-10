import { useState } from 'react';
import {
  Dialog, DialogTitle, DialogContent, DialogActions,
  Button, TextField, Stack, Typography,
} from '@mui/material';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreate: (config: Record<string, number>) => void;
}

const ROLE_FIELDS = [
  { key: 'num_werewolves', label: '狼人' },
  { key: 'num_villagers', label: '平民' },
  { key: 'num_seers', label: '预言家' },
  { key: 'num_witches', label: '女巫' },
  { key: 'num_hunters', label: '猎人' },
];

export default function CreateGame({ open, onClose, onCreate }: Props) {
  const [config, setConfig] = useState({
    num_werewolves: 3,
    num_villagers: 3,
    num_seers: 1,
    num_witches: 1,
    num_hunters: 1,
  });

  const set = (key: string) => (e: React.ChangeEvent<HTMLInputElement>) => {
    setConfig((c) => ({ ...c, [key]: parseInt(e.target.value) || 0 }));
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>创建新游戏</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          配置各角色数量
        </Typography>
        <Stack spacing={2}>
          {ROLE_FIELDS.map(({ key, label }) => (
            <TextField
              key={key}
              label={label}
              type="number"
              value={config[key as keyof typeof config]}
              onChange={set(key)}
              size="small"
              slotProps={{ htmlInput: { min: 0, max: 9 } }}
            />
          ))}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} color="inherit">取消</Button>
        <Button onClick={() => onCreate(config)} variant="contained" disableElevation>
          创建
        </Button>
      </DialogActions>
    </Dialog>
  );
}
