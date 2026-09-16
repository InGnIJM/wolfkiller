export function nextAssignmentCount(
  current: number,
  delta: number,
  assignedTotal: number,
  totalPlayers: number,
  canIncrease = true,
): number {
  if (delta >= 0) {
    if (!canIncrease) return current;
    const headroom = Math.max(0, totalPlayers - assignedTotal);
    return current + Math.min(delta, headroom);
  }
  return Math.max(0, current + delta);
}

export function parseAssignmentCount(
  raw: string,
  current: number,
  assignedTotal: number,
  totalPlayers: number,
  canIncrease = true,
): number | null {
  const trimmed = raw.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const requested = Number.parseInt(trimmed, 10);
  const max = canIncrease
    ? current + Math.max(0, totalPlayers - assignedTotal)
    : current;
  return Math.min(requested, max);
}
