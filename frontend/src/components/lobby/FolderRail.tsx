import { useTranslation } from 'react-i18next';
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
  const { t } = useTranslation();
  return (
    <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap', mb: 2, alignItems: 'center' }}>
      <Chip
        label={t('lobby.allGames')}
        color={filter === 'all' ? 'primary' : 'default'}
        variant={filter === 'all' ? 'filled' : 'outlined'}
        onClick={() => onFilter('all')}
      />
      <Chip
        label={t('lobby.uncategorized')}
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
            label={t('lobby.folderName')}
            value={draftName}
            onChange={(event) => onDraftName(event.target.value)}
          />
          <Button size="small" onClick={onCreate}>{t('lobby.create')}</Button>
          <Button size="small" onClick={onCancelCreate}>{t('common.cancel')}</Button>
        </Box>
      ) : (
        <Button size="small" onClick={onStartCreate}>{t('lobby.newFolder')}</Button>
      )}
    </Stack>
  );
}
