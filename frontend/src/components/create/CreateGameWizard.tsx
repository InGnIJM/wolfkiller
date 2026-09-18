import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Alert, Box, Button, Container, Step, StepLabel, Stepper, Typography,
} from '@mui/material';

import RoleStep from './RoleStep';
import ModelStep from './ModelStep';
import { createGame } from '../../api/client';
import { useModelConfigStore } from '../../store/modelConfigStore';
import type { FieldConstraints, ModelAssignment, RoleCatalogItem } from '../../store/types';

export default function CreateGameWizard() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [roleCounts, setRoleCounts] = useState<Record<string, number>>({});
  const [constraints, setConstraints] = useState<FieldConstraints | null>(null);
  const [roles, setRoles] = useState<RoleCatalogItem[]>([]);
  const [modelAssignments, setModelAssignments] = useState<ModelAssignment[]>([]);
  const [modelAssignmentsInitialized, setModelAssignmentsInitialized] = useState(false);
  const [revealOnDeath, setRevealOnDeath] = useState(false);
  const [enableSheriff, setEnableSheriff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const modelConfigs = useModelConfigStore((state) => state.configs);
  const modelConfigsLoading = useModelConfigStore((state) => state.loading);
  const modelConfigsLoadError = useModelConfigStore((state) => state.loadError);

  const total = Object.values(roleCounts).reduce((sum, n) => sum + n, 0);

  const roleValid = (() => {
    if (!constraints || total === 0) return false;
    if (total < constraints.min_players || total > constraints.max_players) return false;
    // 狼队按阵营汇总（含白狼王等狼阵营变体），目录未返回时回退为普通狼人
    const wolfRoleIds = roles.length
      ? roles.filter((role) => role.camp === 'werewolf').map((role) => role.role_id)
      : ['wolf-killer-werewolf'];
    const wolves = wolfRoleIds.reduce((sum, roleId) => sum + (roleCounts[roleId] ?? 0), 0);
    if (wolves < constraints.min_werewolves) return false;
    if (total - wolves < constraints.min_good) return false;
    return true;
  })();

  const assignedTotal = modelAssignments.reduce(
    (sum, assignment) => sum + Math.max(0, assignment.count),
    0,
  );
  const modelAssignmentsValid = total > 0
    && assignedTotal === total
    && modelAssignments.some((assignment) => assignment.count > 0)
    && modelAssignments.every((assignment) => {
      if (assignment.count <= 0 || assignment.config_id === null) return true;
      if (modelConfigsLoading || modelConfigsLoadError) return false;
      const config = modelConfigs.find((entry) => entry.id === assignment.config_id);
      return Boolean(config && !config.key_invalid);
    });

  const handleNext = () => {
    if (!modelAssignmentsInitialized) {
      setModelAssignments([{ config_id: null, count: total }]);
      setModelAssignmentsInitialized(true);
    }
    setStep(1);
  };

  const handleCreate = async () => {
    setCreating(true);
    setError(null);
    try {
      const res = await createGame({
        role_counts: roleCounts,
        reveal_on_death: revealOnDeath,
        enable_sheriff: enableSheriff,
        model_assignments: modelAssignments.filter((assignment) => assignment.count > 0),
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
          onRolesChange={setRoles}
          revealOnDeath={revealOnDeath}
          onRevealOnDeathChange={setRevealOnDeath}
          enableSheriff={enableSheriff}
          onEnableSheriffChange={setEnableSheriff}
        />
      )}
      {step === 1 && (
        <ModelStep
          totalPlayers={total}
          assignments={modelAssignments}
          onAssignmentsChange={setModelAssignments}
          disabled={creating}
        />
      )}

      <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 3 }}>
        <Button color="inherit" disabled={step === 0 || creating} onClick={() => setStep(0)}>
          上一步
        </Button>
        {step === 0 ? (
          <Button
            variant="contained"
            disableElevation
            disabled={!roleValid}
            onClick={handleNext}
          >
            下一步
          </Button>
        ) : (
          <Button
            variant="contained"
            disableElevation
            disabled={creating || !modelAssignmentsValid}
            aria-busy={creating}
            onClick={() => void handleCreate()}
          >
            {creating ? '创建中…' : '创建游戏'}
          </Button>
        )}
      </Box>
    </Container>
  );
}
