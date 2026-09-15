import { Box, Button, Typography } from '@mui/material';

interface Props {
  selectedCount: number;
  onMove: () => void;
  onDelete: () => void;
  onClear: () => void;
}

export default function BatchActionBar({ selectedCount, onMove, onDelete, onClear }: Props) {
  if (selectedCount === 0) return null;
  return (
    <Box
      sx={{
        position: 'sticky',
        bottom: 16,
        mt: 2,
        px: 2,
        py: 1.5,
        display: 'flex',
        flexWrap: 'wrap',
        gap: 1,
        alignItems: 'center',
        bgcolor: 'background.paper',
        border: '1px solid',
        borderColor: 'divider',
        borderRadius: 2,
      }}
    >
      <Typography variant="body2" sx={{ mr: 'auto' }}>已选 {selectedCount} 局</Typography>
      <Button size="small" onClick={onMove}>批量移动</Button>
      <Button size="small" color="error" onClick={onDelete}>批量删除</Button>
      <Button size="small" onClick={onClear}>取消选择</Button>
    </Box>
  );
}
