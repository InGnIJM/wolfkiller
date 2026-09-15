import type { ModelSnapshotEntry } from './types';

export interface SeatModelInfo {
  name: string;
  model_id: string;
  provider_profile: string;
}

export function seatModelLookup(
  snapshot: ModelSnapshotEntry[] | null | undefined,
): Record<number, SeatModelInfo> {
  const mapped: Record<number, SeatModelInfo> = {};
  if (!Array.isArray(snapshot)) return mapped;
  for (const entry of snapshot) {
    if (!Array.isArray(entry.seats) || entry.seats.length === 0) continue;
    for (const seat of entry.seats) {
      if (!Number.isInteger(seat) || seat < 1) continue;
      mapped[seat] = {
        name: entry.name,
        model_id: entry.model_id,
        provider_profile: entry.provider_profile,
      };
    }
  }
  return mapped;
}
