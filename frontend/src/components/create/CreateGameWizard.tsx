import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Box, Button, Container, Step, StepLabel, Stepper, Typography,
} from '@mui/material';

import RoleStep from './RoleStep';
import ModelStep from './ModelStep';
import { createGame } from '../../api/client';
import type { FieldConstraints } from '../../store/types';

export default function CreateGameWizard() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [roleCounts, setRoleCounts] = useState<Record<string, number>>({});
  const [constraints, setConstraints] = useState<FieldConstraints | null>(null);
  const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const total = Object.values(roleCounts).reduce((sum, n) => sum + n, 0);

  const roleValid = (() => {
    if (!constraints || total === 0) return false;
    if (total < constraints.min_players || total > constraints.max_players) return false;
    const wolves = roleCounts['wolf-killer-werewolf'] ?? 0;
    if (wolves < constraints.min_werewolves) return false;
    if (total - wolves < constraints.min_good) return false;
    return true;
  })();

  const handleCreate = async () => {
    setCreating(true);
    setError(null);
    try {
      const res = await createGame({
        role_counts: roleCounts,
        model_assignments: [{ config_id: selectedModelId, count: total }],
      });
      navigate(`/game/${res.game_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setCreating(false);
    }
  };

  return (
    <Container maxWidth="md" sx={{ py: 4 }}>
      <Typography variant="h4" sx={{ fontWeight: 400, mb: 2 }}>创建游戏</Typography>
      <Stepper activeStep={step} sx={{ mb: 4 }}>
        <Step><StepLabel>人数身份配置</StepLabel></Step>
        <Step><StepLabel>Agent 模型配置</StepLabel></Step>
      </Stepper>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {step === 0 && (
        <RoleStep
          roleCounts={roleCounts}
          onRoleCountsChange={setRoleCounts}
          onConstraintsChange={setConstraints}
        />
      )}
      {step === 1 && (
        <ModelStep
          totalPlayers={total}
          selectedModelId={selectedModelId}
          onSelect={setSelectedModelId}
        />
      )}

      <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 3 }}>
        <Button color="inherit" disabled={step === 0} onClick={() => setStep(0)}>
          上一步
        </Button>
        {step === 0 ? (
          <Button
            variant="contained"
            disableElevation
            disabled={!roleValid}
            onClick={() => setStep(1)}
          >
            下一步
          </Button>
        ) : (
          <Button
            variant="contained"
            disableElevation
            disabled={creating}
            onClick={() => void handleCreate()}
          >
            创建游戏
          </Button>
        )}
      </Box>
    </Container>
  );
}
