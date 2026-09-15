import { Box, Button, Chip, Stack, TextField } from '@mui/material';
import type { GameFolder } from '../../store/types';

export type FolderFilter = 'all' | 'unfiled' | string;

interface Props {
  folders: GameFolder[];
  filter: FolderFilter;
  creating: boolean;
  draftName: string;
  onFilter: (filter: FolderFilter) => void;
  onStartCreate: () => void;
  onDraftName: (value: string) => void;
  onCreate: () => void;
  onCancelCreate: () => void;
}

export default function FolderRail({
  folders, filter, creating, draftName, onFilter, onStartCreate,
  onDraftName, onCreate, onCancelCreate,
}: Props) {
  return (
    <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap', mb: 2, alignItems: 'center' }}>
      <Chip
        label="全部"
        color={filter === 'all' ? 'primary' : 'default'}
        variant={filter === 'all' ? 'filled' : 'outlined'}
        onClick={() => onFilter('all')}
      />
      <Chip
        label="未分类"
        color={filter === 'unfiled' ? 'primary' : 'default'}
        variant={filter === 'unfiled' ? 'filled' : 'outlined'}
        onClick={() => onFilter('unfiled')}
      />
      {folders.map((folder) => (
        <Chip
          key={folder.folder_id}
          label={`${folder.name} (${folder.game_count})`}
          color={filter === folder.folder_id ? 'primary' : 'default'}
          variant={filter === folder.folder_id ? 'filled' : 'outlined'}
          onClick={() => onFilter(folder.folder_id)}
        />
      ))}
      {creating ? (
        <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
          <TextField
            size="small"
            label="文件夹名称"
            value={draftName}
            onChange={(event) => onDraftName(event.target.value)}
          />
          <Button size="small" onClick={onCreate}>创建</Button>
          <Button size="small" onClick={onCancelCreate}>取消</Button>
        </Box>
      ) : (
        <Button size="small" onClick={onStartCreate}>新建文件夹</Button>
      )}
    </Stack>
  );
}
