const API_BASE = 'http://localhost:8000';

export async function createGame(config?: {
  num_werewolves?: number;
  num_villagers?: number;
  num_seers?: number;
  num_witches?: number;
  num_hunters?: number;
}): Promise<{ game_id: string }> {
  const res = await fetch(`${API_BASE}/api/games`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config || {}),
  });
  if (!res.ok) throw new Error(`Create game failed: ${res.status}`);
  return res.json();
}

export async function listGames(): Promise<{ games: any[] }> {
  const res = await fetch(`${API_BASE}/api/games`);
  if (!res.ok) throw new Error(`List games failed: ${res.status}`);
  return res.json();
}

export async function fetchGameLogs(gameId: string): Promise<{
  game_id: string;
  conversations: any[];
  operations: any[];
}> {
  const res = await fetch(`${API_BASE}/api/games/${gameId}/logs`);
  if (!res.ok) throw new Error(`Fetch logs failed: ${res.status}`);
  return res.json();
}

export async function fetchGameDetail(gameId: string): Promise<{
  game_id: string;
  phase: string;
  round_number: number;
  players: Record<number, {
    seat_number: number;
    role: string;
    camp: string;
    is_alive: boolean;
    has_antidote: boolean;
    has_poison: boolean;
    has_gun: boolean;
    revealed_role: string | null;
    is_sheriff: boolean;
  }>;
  win_result: { winning_camp: string; reason: string } | null;
}> {
  const res = await fetch(`${API_BASE}/api/games/${gameId}`);
  if (!res.ok) throw new Error(`Fetch game detail failed: ${res.status}`);
  return res.json();
}

export function getWsUrl(gameId: string): string {
  return `ws://localhost:8000/ws/game/${gameId}`;
}
