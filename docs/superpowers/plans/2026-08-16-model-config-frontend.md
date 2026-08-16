# 模型配置前端 实施计划（一期）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引入 react-router（/ /models /create /game/:gameId），交付模型管理页（列表 + 新建/编辑对话框 + 连接测试）与两步创建向导（预设/角色加减 → 单模型选择 + 当场新建），并打通创建游戏 API。

**Architecture:** `AppShell` 承载路由（`GameBoard` 保持 prop 形态不动）；新增 `modelConfigStore`（Zustand）承载模型配置 CRUD 状态；`ModelConfigPage` + `ModelConfigDialog` 实现管理页；`CreateGameWizard` 组合 `RoleStep`（预设卡片 + 角色加减 + 硬约束禁用）与 `ModelStep`（环境默认 + 配置单选 + 当场新建）。一期为单选卡片，二期在原位换数量 stepper。

**Tech Stack:** React 19、TypeScript、MUI 9、Zustand 5、react-router-dom 7（新增依赖）、Vitest 4 + @testing-library。

**规格依据:** `docs/superpowers/specs/2026-08-16-model-config-game-creation-design.md`
**后端计划:** `docs/superpowers/plans/2026-08-16-model-config-backend.md`（先完成后端再实施前端）

**约定（每个 Task 通用）：**
- 测试文件与被测组件同目录的 `test/` 子目录，首行 `// @vitest-environment jsdom`
- 运行单个测试：`npx vitest run src/<path>`；全量：`npm test`；静态检查：`npm run lint`；构建：`npm run build`
- 每个 commit ≤ 3 个文件；message 格式 `<type>(<scope>): <summary>`
- 角色图标暂不渲染 emoji/Material Symbols（沿用中文名 + 阵营 chip），不动现有 `RoleIcon`（规避全局图标规范冲突，见规格风险 9）

---

### Task 1: react-router 路由与页面骨架

**Files:**
- Modify: `frontend/package.json`（+ react-router-dom）
- Modify: `frontend/package-lock.json`（npm install 自动更新）
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/components/models/ModelConfigPage.tsx`（暂为 stub）
- Create: `frontend/src/components/create/CreateGameWizard.tsx`（暂为 stub）
- Modify: `frontend/src/components/lobby/GameList.tsx`
- Modify: `frontend/src/components/lobby/test/GameList.test.tsx`
- Delete: `frontend/src/components/lobby/CreateGame.tsx`
- Test: `frontend/src/test/App.test.tsx`

- [ ] **Step 1: 安装依赖**

Run: `npm install react-router-dom@^7`
Expected: 安装成功，package.json 出现 `"react-router-dom": "^7.x"`

- [ ] **Step 2: 写失败测试（路由）**

创建 `frontend/src/test/App.test.tsx`：

```tsx
// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { AppShell } from '../App';

vi.mock('../components/lobby/GameList', () => ({
  default: ({
    onJoinGame,
    onCreateClick,
  }: {
    onJoinGame: (id: string) => void;
    onCreateClick: () => void;
  }) => (
    <div>
      <button onClick={() => onJoinGame('g1')}>join-g1</button>
      <button onClick={onCreateClick}>open-create</button>
    </div>
  ),
}));

vi.mock('../components/game/GameBoard', () => ({
  default: ({ gameId }: { gameId: string }) => <div data-testid={`board-${gameId}`} />,
}));

vi.mock('../components/models/ModelConfigPage', () => ({
  default: () => <div data-testid="models-page" />,
}));

vi.mock('../components/create/CreateGameWizard', () => ({
  default: () => <div data-testid="wizard-page" />,
}));

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppShell />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('AppShell routing', () => {
  it('renders lobby at / and joins a game', () => {
    renderAt('/');
    expect(screen.getByRole('button', { name: 'join-g1' })).toBeInTheDocument();
  });

  it('opens the create wizard from the lobby', () => {
    const navigate = vi.fn();
    vi.mock('react-router-dom', async (importOriginal) => {
      const actual = await importOriginal<typeof import('react-router-dom')>();
      return { ...actual, useNavigate: () => navigate };
    });
    // 说明：此用例改为在 AppShell 内点击 open-create 后断言调用（见 Step 3 实现后的补充断言）
    renderAt('/');
    expect(screen.getByRole('button', { name: 'open-create' })).toBeInTheDocument();
  });

  it('renders models page at /models', () => {
    renderAt('/models');
    expect(screen.getByTestId('models-page')).toBeInTheDocument();
  });

  it('renders wizard at /create', () => {
    renderAt('/create');
    expect(screen.getByTestId('wizard-page')).toBeInTheDocument();
  });

  it('renders game board at /game/:gameId', () => {
    renderAt('/game/abc');
    expect(screen.getByTestId('board-abc')).toBeInTheDocument();
  });

  it('renders the 模型管理 nav button', () => {
    renderAt('/');
    expect(screen.getByRole('button', { name: '模型管理' })).toBeInTheDocument();
  });
});
```

注意：`vi.mock` 是提升执行的，不能在测试用例内部调用；上面第二个用例的 `vi.mock` 写法非法。改为在文件顶部与其余 mock 并列声明导航 mock：

```tsx
const navigateMock = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigateMock };
});
```

并把「opens the create wizard」用例改为：

```tsx
  it('opens the create wizard from the lobby', () => {
    renderAt('/');
    fireEvent.click(screen.getByRole('button', { name: 'open-create' }));
    expect(navigateMock).toHaveBeenCalledWith('/create');
  });
```

（需在顶部 import 中加 `fireEvent`。）

- [ ] **Step 3: 运行确认失败**

Run: `npx vitest run src/test/App.test.tsx`
Expected: FAIL（AppShell 未导出 / 路由不存在）

- [ ] **Step 4: 实现路由与 stub 页面**

创建 `frontend/src/components/models/ModelConfigPage.tsx`：

```tsx
export default function ModelConfigPage() {
  return <div>模型管理（建设中）</div>;
}
```

创建 `frontend/src/components/create/CreateGameWizard.tsx`：

```tsx
export default function CreateGameWizard() {
  return <div>创建游戏（建设中）</div>;
}
```

用以下完整内容替换 `frontend/src/App.tsx`：

```tsx
import {
  BrowserRouter, Link, Navigate, Route, Routes, useNavigate, useParams,
} from 'react-router-dom';
import {
  ThemeProvider, CssBaseline, Box, Typography, AppBar, Toolbar,
  Container, Button,
} from '@mui/material';
import theme from './theme';
import GameList from './components/lobby/GameList';
import GameBoard from './components/game/GameBoard';
import ModelConfigPage from './components/models/ModelConfigPage';
import CreateGameWizard from './components/create/CreateGameWizard';

function GameRoute() {
  const { gameId } = useParams<{ gameId: string }>();
  const navigate = useNavigate();
  if (!gameId) return <Navigate to="/" replace />;
  return <GameBoard gameId={gameId} onBack={() => navigate('/')} />;
}

export function AppShell() {
  const navigate = useNavigate();
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box sx={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
        <AppBar
          position="static"
          elevation={0}
          sx={{
            bgcolor: 'background.paper',
            borderBottom: '1px solid',
            borderColor: 'divider',
          }}
        >
          <Container maxWidth={false}>
            <Toolbar disableGutters sx={{ minHeight: 56 }}>
              <Typography
                component={Link}
                to="/"
                variant="h6"
                sx={{
                  flexGrow: 1,
                  fontWeight: 500,
                  fontSize: '1.125rem',
                  letterSpacing: '-0.2px',
                  color: 'text.primary',
                  textDecoration: 'none',
                }}
              >
                Wolf Killer
              </Typography>
              <Button color="inherit" onClick={() => navigate('/models')}>
                模型管理
              </Button>
            </Toolbar>
          </Container>
        </AppBar>
        <Box sx={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <Routes>
            <Route
              path="/"
              element={(
                <GameList
                  onJoinGame={(id) => navigate(`/game/${id}`)}
                  onCreateClick={() => navigate('/create')}
                />
              )}
            />
            <Route path="/models" element={<ModelConfigPage />} />
            <Route path="/create" element={<CreateGameWizard />} />
            <Route path="/game/:gameId" element={<GameRoute />} />
          </Routes>
        </Box>
      </Box>
    </ThemeProvider>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppShell />
    </BrowserRouter>
  );
}
```

- [ ] **Step 5: 改造 GameList（去掉弹窗，改用 onCreateClick）**

用以下完整内容替换 `frontend/src/components/lobby/GameList.tsx`：

```tsx
import { useEffect, useState } from 'react';
import { Box, Typography, Button, Stack, Container } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import GameCard from './GameCard';
import { listGames } from '../../api/client';

interface GameInfo {
  game_id: string;
  phase: string;
  round_number: number;
  player_count: number;
  alive_count: number;
  winner: string | null;
}

interface Props {
  onJoinGame: (gameId: string) => void;
  onCreateClick: () => void;
}

const PHASE_LABELS: Record<string, string> = {
  waiting: '等待中',
  role_deal: '分配角色',
  night: '黑夜',
  dawn: '天亮',
  last_words: '遗言',
  speech: '发言',
  vote_casting: '投票',
  vote_resolution: '公布结果',
  game_over: '已结束',
};

export default function GameList({ onJoinGame, onCreateClick }: Props) {
  const [games, setGames] = useState<GameInfo[]>([]);

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const res = await listGames();
        if (active) setGames(res.games);
      } catch (e) {
        if (active) console.error('Failed to list games:', e);
      }
    };

    void Promise.resolve().then(refresh);
    const t = setInterval(() => void refresh(), 3000);
    return () => {
      active = false;
      clearInterval(t);
    };
  }, []);

  return (
    <Container maxWidth="sm" sx={{ py: 4 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 4 }}>
        <Box>
          <Typography variant="h4" sx={{ fontWeight: 400, fontSize: '2rem', letterSpacing: '-0.5px' }}>
            狼人杀
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            选择一个对局加入，或创建新游戏
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={onCreateClick}
          disableElevation
        >
          创建游戏
        </Button>
      </Box>

      {games.length === 0 && (
        <Box sx={{ textAlign: 'center', py: 8 }}>
          <Typography variant="h6" color="text.disabled" sx={{ fontWeight: 400, mb: 1 }}>
            暂无对局
          </Typography>
          <Typography variant="body2" color="text.disabled">
            点击「创建游戏」开始一局新的狼人杀
          </Typography>
        </Box>
      )}

      <Stack spacing={1.5}>
        {games.map((g) => (
          <GameCard
            key={g.game_id}
            gameId={g.game_id}
            phase={PHASE_LABELS[g.phase] || g.phase}
            roundNumber={g.round_number}
            playerCount={g.player_count}
            aliveCount={g.alive_count}
            winner={g.winner}
            onClick={() => onJoinGame(g.game_id)}
          />
        ))}
      </Stack>
    </Container>
  );
}
```

删除 `frontend/src/components/lobby/CreateGame.tsx`（`git rm`）。

- [ ] **Step 6: 重写 GameList 测试**

用以下完整内容替换 `frontend/src/components/lobby/test/GameList.test.tsx`：

```tsx
// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { listGames } from '../../../api/client';
import GameList from '../GameList';

vi.mock('../../../api/client', () => ({
  listGames: vi.fn(),
}));

vi.mock('../GameCard', () => ({
  default: ({
    gameId,
    phase,
    onClick,
  }: {
    gameId: string;
    phase: string;
    onClick: () => void;
  }) => (
    <button data-testid={`game-${gameId}`} data-phase={phase} onClick={onClick}>
      {gameId}
    </button>
  ),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const game = (id: string, phase = 'night') => ({
  game_id: id,
  phase,
  round_number: 1,
  player_count: 6,
  alive_count: 6,
  winner: null,
});

beforeEach(() => {
  vi.mocked(listGames).mockResolvedValue({ games: [] });
});

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('GameList polling', () => {
  it('loads immediately and refreshes the visible games every three seconds', async () => {
    vi.useFakeTimers();
    vi.mocked(listGames)
      .mockResolvedValueOnce({ games: [game('game-1')] })
      .mockResolvedValueOnce({ games: [game('game-2', 'custom-phase' as never)] });
    const onJoinGame = vi.fn();

    render(<GameList onJoinGame={onJoinGame} onCreateClick={vi.fn()} />);

    await act(async () => Promise.resolve());
    expect(listGames).toHaveBeenCalledOnce();
    expect(screen.getByTestId('game-game-1')).toHaveAttribute('data-phase', '黑夜');
    fireEvent.click(screen.getByTestId('game-game-1'));
    expect(onJoinGame).toHaveBeenCalledWith('game-1');

    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(listGames).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('game-game-2')).toHaveAttribute('data-phase', 'custom-phase');
  });

  it('clears polling and ignores an in-flight rejection after unmount', async () => {
    vi.useFakeTimers();
    const pending = deferred<{ games: [] }>();
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(listGames).mockReturnValueOnce(pending.promise);

    const { unmount } = render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    expect(listGames).toHaveBeenCalledOnce();

    unmount();
    await act(async () => pending.reject(new Error('late failure')));
    await act(async () => vi.advanceTimersByTimeAsync(3000));

    expect(errorSpy).not.toHaveBeenCalled();
    expect(listGames).toHaveBeenCalledOnce();
  });

  it('ignores an in-flight successful refresh after unmount', async () => {
    const pending = deferred<{ games: Array<ReturnType<typeof game>> }>();
    vi.mocked(listGames).mockReturnValueOnce(pending.promise);

    const { unmount } = render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());
    unmount();
    await act(async () =>
      pending.resolve({ games: [game('late-game')] }),
    );

    expect(screen.queryByTestId('game-late-game')).not.toBeInTheDocument();
  });

  it('reports a refresh error while mounted and keeps polling', async () => {
    vi.useFakeTimers();
    const error = new Error('temporary failure');
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    vi.mocked(listGames).mockRejectedValueOnce(error).mockResolvedValueOnce({ games: [] });

    render(<GameList onJoinGame={vi.fn()} onCreateClick={vi.fn()} />);
    await act(async () => Promise.resolve());

    expect(errorSpy).toHaveBeenCalledWith('Failed to list games:', error);
    await act(async () => vi.advanceTimersByTimeAsync(3000));
    expect(listGames).toHaveBeenCalledTimes(2);
  });

  it('opens the create wizard through onCreateClick', async () => {
    const onCreateClick = vi.fn();
    render(<GameList onJoinGame={vi.fn()} onCreateClick={onCreateClick} />);
    await act(async () => Promise.resolve());

    fireEvent.click(screen.getByRole('button', { name: /创建游戏/ }));
    expect(onCreateClick).toHaveBeenCalledOnce();
  });
});
```

- [ ] **Step 7: 运行确认通过**

Run: `npx vitest run src/test/App.test.tsx src/components/lobby/test/GameList.test.tsx`
Expected: PASS

Run: `npm run lint`
Expected: 无错误（stub 组件无 unused 变量）

- [ ] **Step 8: Commit（分三个 commit）**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore(frontend): add react-router-dom dependency"

git add frontend/src/App.tsx frontend/src/components/models/ModelConfigPage.tsx frontend/src/components/create/CreateGameWizard.tsx
git commit -m "feat(web): add router shell with model and create routes"

git add frontend/src/components/lobby/GameList.tsx frontend/src/components/lobby/test/GameList.test.tsx frontend/src/test/App.test.tsx
git rm frontend/src/components/lobby/CreateGame.tsx
git commit -m "refactor(lobby): route game creation to the wizard page"
```

---

### Task 2: 类型定义与 API client 扩展

**Files:**
- Modify: `frontend/src/store/types.ts`
- Modify: `frontend/src/api/client.ts`
- Test: `frontend/src/api/test/client.test.ts`

- [ ] **Step 1: 写失败测试**

创建 `frontend/src/api/test/client.test.ts`：

```ts
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  createGame, createModel, deleteModel, fetchConstraints, fetchPresets,
  fetchRoleCatalog, listModels, testModelConnection, updateModel,
} from '../client';

const BASE = 'http://localhost:8000';

function mockFetch(body: unknown, ok = true, status = 200) {
  const fn = vi.fn(async () => ({ ok, status, json: async () => body }));
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('model config api', () => {
  it('lists models', async () => {
    const fetchFn = mockFetch([{ id: 'a', name: 'n' }]);
    const result = await listModels();
    expect(fetchFn).toHaveBeenCalledWith(`${BASE}/api/models`);
    expect(result).toEqual([{ id: 'a', name: 'n' }]);
  });

  it('creates a model with JSON body', async () => {
    const fetchFn = mockFetch({ id: 'a', name: 'n' });
    const result = await createModel({
      name: 'n', base_url: 'https://x', model_id: 'm', api_key: 'sk-k',
    });
    expect(result).toEqual({ id: 'a', name: 'n' });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe(`${BASE}/api/models`);
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({
      name: 'n', base_url: 'https://x', model_id: 'm', api_key: 'sk-k',
    });
  });

  it('updates and deletes a model', async () => {
    const fetchFn = mockFetch({ id: 'a' });
    await updateModel('a', { name: 'n', base_url: 'https://x', model_id: 'm' });
    expect(fetchFn.mock.calls[0][0]).toBe(`${BASE}/api/models/a`);
    expect(fetchFn.mock.calls[0][1].method).toBe('PUT');

    await deleteModel('a');
    expect(fetchFn.mock.calls[1][0]).toBe(`${BASE}/api/models/a`);
    expect(fetchFn.mock.calls[1][1].method).toBe('DELETE');
  });

  it('tests model connection', async () => {
    const fetchFn = mockFetch({ ok: true, latency_ms: 42, error: null });
    const result = await testModelConnection({ config_id: 'a' });
    expect(result).toEqual({ ok: true, latency_ms: 42, error: null });
    expect(fetchFn.mock.calls[0][0]).toBe(`${BASE}/api/models/test`);
  });

  it('fetches role catalog and unwraps the envelope', async () => {
    mockFetch({ roles: [{ role_id: 'wolf-killer-werewolf' }] });
    const result = await fetchRoleCatalog();
    expect(result).toEqual([{ role_id: 'wolf-killer-werewolf' }]);
  });

  it('fetches presets and constraints', async () => {
    const fetchFn = mockFetch({ presets: [{ id: 'p' }] });
    expect(await fetchPresets()).toEqual([{ id: 'p' }]);
    expect(fetchFn.mock.calls[0][0]).toBe(`${BASE}/api/catalog/presets`);

    const fetchFn2 = mockFetch({ min_players: 4, max_players: 12, min_werewolves: 1, min_good: 1 });
    expect(await fetchConstraints()).toEqual({
      min_players: 4, max_players: 12, min_werewolves: 1, min_good: 1,
    });
    expect(fetchFn2.mock.calls[0][0]).toBe(`${BASE}/api/catalog/constraints`);
  });

  it('createGame posts role_counts and model_assignments', async () => {
    const fetchFn = mockFetch({ game_id: 'g', model_snapshot: [] });
    const result = await createGame({
      role_counts: { 'wolf-killer-werewolf': 1 },
      model_assignments: [{ config_id: null, count: 1 }],
    });
    expect(result).toEqual({ game_id: 'g', model_snapshot: [] });
    const [url, init] = fetchFn.mock.calls[0];
    expect(url).toBe(`${BASE}/api/games`);
    expect(JSON.parse(init.body)).toEqual({
      role_counts: { 'wolf-killer-werewolf': 1 },
      model_assignments: [{ config_id: null, count: 1 }],
    });
  });

  it('rejects non-ok responses', async () => {
    mockFetch({}, false, 500);
    await expect(listModels()).rejects.toThrow('List models failed: 500');
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/api/test/client.test.ts`
Expected: FAIL（listModels 等未导出 / 类型不匹配）

- [ ] **Step 3: 追加类型**

在 `frontend/src/store/types.ts` 末尾追加：

```ts
// ── Model config & game creation catalog ──────────────────────

export interface ModelConfig {
  id: string;
  name: string;
  base_url: string;
  model_id: string;
  has_key: boolean;
  api_key_masked: string | null;
  key_invalid: boolean;
  temperature: number | null;
  strict_base_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface ModelConfigInput {
  name: string;
  base_url: string;
  model_id: string;
  api_key?: string;
  temperature?: number | null;
  strict_base_url?: string | null;
}

export interface ModelTestResult {
  ok: boolean;
  latency_ms: number | null;
  error: string | null;
}

export interface RoleCatalogItem {
  role_id: string;
  display_name: string;
  name_zh: string;
  camp: string;
  icon: string;
  description: string;
  min_count: number;
  max_count: number | null;
  dependencies: string[];
  exclusions: string[];
}

export interface GamePreset {
  id: string;
  name: string;
  description: string;
  role_counts: Record<string, number>;
}

export interface FieldConstraints {
  min_players: number;
  max_players: number;
  min_werewolves: number;
  min_good: number;
}

export interface ModelAssignment {
  config_id: string | null;
  count: number;
}

export interface ModelSnapshotEntry {
  config_id: string | null;
  name: string;
  model_id: string;
  base_url: string;
}
```

- [ ] **Step 4: 扩展 client**

`frontend/src/api/client.ts`：

① 顶部类型导入替换为：

```ts
import type {
  FieldConstraints, GameListResponse, GameLogs, GameMemories, GamePreset,
  ModelAssignment, ModelConfig, ModelConfigInput, ModelSnapshotEntry,
  ModelTestResult, PublicGameState, RoleCatalogItem,
} from '../store/types';
```

② `createGame` 整体替换为：

```ts
export async function createGame(config?: {
  role_counts?: Record<string, number>;
  model_assignments?: ModelAssignment[];
}): Promise<{ game_id: string; model_snapshot?: ModelSnapshotEntry[] }> {
  const res = await fetch(`${getApiBase()}/api/games`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config || {}),
  });
  if (!res.ok) throw new Error(`Create game failed: ${res.status}`);
  return res.json();
}
```

③ 文件末尾追加：

```ts
export async function listModels(): Promise<ModelConfig[]> {
  const res = await fetch(`${getApiBase()}/api/models`);
  if (!res.ok) throw new Error(`List models failed: ${res.status}`);
  return res.json();
}

export async function createModel(input: ModelConfigInput): Promise<ModelConfig> {
  const res = await fetch(`${getApiBase()}/api/models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Create model failed: ${res.status}`);
  return res.json();
}

export async function updateModel(id: string, input: ModelConfigInput): Promise<ModelConfig> {
  const res = await fetch(`${getApiBase()}/api/models/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Update model failed: ${res.status}`);
  return res.json();
}

export async function deleteModel(id: string): Promise<void> {
  const res = await fetch(`${getApiBase()}/api/models/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Delete model failed: ${res.status}`);
}

export async function testModelConnection(input: {
  config_id?: string | null;
  base_url?: string;
  api_key?: string;
  model_id?: string;
}): Promise<ModelTestResult> {
  const res = await fetch(`${getApiBase()}/api/models/test`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) throw new Error(`Test model failed: ${res.status}`);
  return res.json();
}

export async function fetchRoleCatalog(): Promise<RoleCatalogItem[]> {
  const res = await fetch(`${getApiBase()}/api/catalog/roles`);
  if (!res.ok) throw new Error(`Fetch roles failed: ${res.status}`);
  const data = (await res.json()) as { roles: RoleCatalogItem[] };
  return data.roles;
}

export async function fetchPresets(): Promise<GamePreset[]> {
  const res = await fetch(`${getApiBase()}/api/catalog/presets`);
  if (!res.ok) throw new Error(`Fetch presets failed: ${res.status}`);
  const data = (await res.json()) as { presets: GamePreset[] };
  return data.presets;
}

export async function fetchConstraints(): Promise<FieldConstraints> {
  const res = await fetch(`${getApiBase()}/api/catalog/constraints`);
  if (!res.ok) throw new Error(`Fetch constraints failed: ${res.status}`);
  return res.json();
}
```

- [ ] **Step 5: 运行确认通过**

Run: `npx vitest run src/api/test/client.test.ts`
Expected: PASS

Run: `npm run lint`
Expected: 无错误

- [ ] **Step 6: Commit**

```bash
git add frontend/src/store/types.ts frontend/src/api/client.ts frontend/src/api/test/client.test.ts
git commit -m "feat(api): add model config and catalog client functions"
```

---

### Task 3: modelConfigStore

**Files:**
- Create: `frontend/src/store/modelConfigStore.ts`
- Test: `frontend/src/store/test/modelConfigStore.test.ts`

- [ ] **Step 1: 写失败测试**

创建 `frontend/src/store/test/modelConfigStore.test.ts`：

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useModelConfigStore } from '../modelConfigStore';
import { createModel, deleteModel, listModels, updateModel } from '../../api/client';

vi.mock('../../api/client', () => ({
  listModels: vi.fn(),
  createModel: vi.fn(),
  updateModel: vi.fn(),
  deleteModel: vi.fn(),
}));

const sample = {
  id: 'a',
  name: 'n',
  base_url: 'https://x',
  model_id: 'm',
  has_key: false,
  api_key_masked: null,
  key_invalid: false,
  temperature: null,
  strict_base_url: null,
  created_at: '',
  updated_at: '',
};

const input = { name: 'n', base_url: 'https://x', model_id: 'm' };

beforeEach(() => {
  useModelConfigStore.setState({ configs: [], loading: false, error: null });
});

describe('modelConfigStore', () => {
  it('loads configs and clears loading', async () => {
    vi.mocked(listModels).mockResolvedValue([sample]);
    await useModelConfigStore.getState().load();
    expect(useModelConfigStore.getState().configs).toEqual([sample]);
    expect(useModelConfigStore.getState().loading).toBe(false);
  });

  it('stores load errors', async () => {
    vi.mocked(listModels).mockRejectedValue(new Error('boom'));
    await useModelConfigStore.getState().load();
    expect(useModelConfigStore.getState().error).toBe('boom');
    expect(useModelConfigStore.getState().loading).toBe(false);
  });

  it('appends created configs and clears error', async () => {
    useModelConfigStore.setState({ error: 'stale' });
    vi.mocked(createModel).mockResolvedValue(sample);
    const result = await useModelConfigStore.getState().create(input);
    expect(result).toEqual(sample);
    expect(useModelConfigStore.getState().configs).toEqual([sample]);
    expect(useModelConfigStore.getState().error).toBeNull();
  });

  it('returns null and stores error on create failure', async () => {
    vi.mocked(createModel).mockRejectedValue(new Error('dup'));
    const result = await useModelConfigStore.getState().create(input);
    expect(result).toBeNull();
    expect(useModelConfigStore.getState().error).toBe('dup');
  });

  it('replaces updated configs', async () => {
    useModelConfigStore.setState({ configs: [sample] });
    const updated = { ...sample, name: 'n2' };
    vi.mocked(updateModel).mockResolvedValue(updated);
    await useModelConfigStore.getState().update('a', input);
    expect(useModelConfigStore.getState().configs[0].name).toBe('n2');
  });

  it('returns null and stores error on update failure', async () => {
    useModelConfigStore.setState({ configs: [sample] });
    vi.mocked(updateModel).mockRejectedValue(new Error('x'));
    expect(await useModelConfigStore.getState().update('a', input)).toBeNull();
    expect(useModelConfigStore.getState().error).toBe('x');
  });

  it('removes deleted configs', async () => {
    useModelConfigStore.setState({ configs: [sample] });
    vi.mocked(deleteModel).mockResolvedValue(undefined);
    expect(await useModelConfigStore.getState().remove('a')).toBe(true);
    expect(useModelConfigStore.getState().configs).toEqual([]);
  });

  it('returns false and stores error on delete failure', async () => {
    useModelConfigStore.setState({ configs: [sample] });
    vi.mocked(deleteModel).mockRejectedValue(new Error('x'));
    expect(await useModelConfigStore.getState().remove('a')).toBe(false);
    expect(useModelConfigStore.getState().error).toBe('x');
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/store/test/modelConfigStore.test.ts`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 store**

创建 `frontend/src/store/modelConfigStore.ts`：

```ts
import { create } from 'zustand';

import * as api from '../api/client';
import type { ModelConfig, ModelConfigInput } from './types';

interface ModelConfigState {
  configs: ModelConfig[];
  loading: boolean;
  error: string | null;
  load: () => Promise<void>;
  create: (input: ModelConfigInput) => Promise<ModelConfig | null>;
  update: (id: string, input: ModelConfigInput) => Promise<ModelConfig | null>;
  remove: (id: string) => Promise<boolean>;
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export const useModelConfigStore = create<ModelConfigState>((set) => ({
  configs: [],
  loading: false,
  error: null,

  load: async () => {
    set({ loading: true, error: null });
    try {
      const configs = await api.listModels();
      set({ configs, loading: false });
    } catch (error) {
      set({ error: messageOf(error), loading: false });
    }
  },

  create: async (input) => {
    try {
      const config = await api.createModel(input);
      set((state) => ({ configs: [...state.configs, config], error: null }));
      return config;
    } catch (error) {
      set({ error: messageOf(error) });
      return null;
    }
  },

  update: async (id, input) => {
    try {
      const config = await api.updateModel(id, input);
      set((state) => ({
        configs: state.configs.map((c) => (c.id === id ? config : c)),
        error: null,
      }));
      return config;
    } catch (error) {
      set({ error: messageOf(error) });
      return null;
    }
  },

  remove: async (id) => {
    try {
      await api.deleteModel(id);
      set((state) => ({
        configs: state.configs.filter((c) => c.id !== id),
        error: null,
      }));
      return true;
    } catch (error) {
      set({ error: messageOf(error) });
      return false;
    }
  },
}));
```

- [ ] **Step 4: 运行确认通过**

Run: `npx vitest run src/store/test/modelConfigStore.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/store/modelConfigStore.ts frontend/src/store/test/modelConfigStore.test.ts
git commit -m "feat(store): add model config store with CRUD actions"
```

---

### Task 4: 模型管理页（列表 + 对话框）

**Files:**
- Modify: `frontend/src/components/models/ModelConfigPage.tsx`（替换 stub）
- Create: `frontend/src/components/models/ModelConfigDialog.tsx`
- Test: `frontend/src/components/models/test/ModelConfigPage.test.tsx`

- [ ] **Step 1: 写失败测试**

创建 `frontend/src/components/models/test/ModelConfigPage.test.tsx`：

```tsx
// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import ModelConfigPage from '../ModelConfigPage';
import {
  createModel, deleteModel, listModels, testModelConnection, updateModel,
} from '../../../api/client';

vi.mock('../../../api/client', () => ({
  listModels: vi.fn(),
  createModel: vi.fn(),
  updateModel: vi.fn(),
  deleteModel: vi.fn(),
  testModelConnection: vi.fn(),
}));

const sample = {
  id: 'a1',
  name: 'DeepSeek Pro',
  base_url: 'https://api.deepseek.com/v1',
  model_id: 'deepseek-v4-pro',
  has_key: true,
  api_key_masked: 'sk-***1234',
  key_invalid: false,
  temperature: 1.2,
  strict_base_url: null,
  created_at: '2026-08-16T00:00:00',
  updated_at: '2026-08-16T00:00:00',
};

beforeEach(() => {
  vi.mocked(listModels).mockResolvedValue([sample]);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ModelConfigPage', () => {
  it('loads and renders configs on mount', async () => {
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());
    expect(screen.getByText(/sk-\*\*\*1234/)).toBeInTheDocument();
  });

  it('renders the empty state when there are no configs', async () => {
    vi.mocked(listModels).mockResolvedValue([]);
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('暂无模型配置')).toBeInTheDocument());
  });

  it('creates a new config through the dialog', async () => {
    vi.mocked(createModel).mockResolvedValue({ ...sample, id: 'b2', name: 'New Model' });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'New Model' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'sk-new' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(createModel).toHaveBeenCalledWith(expect.objectContaining({
        name: 'New Model', api_key: 'sk-new',
      })),
    );
  });

  it('rejects save when name is blank', async () => {
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(createModel).not.toHaveBeenCalled();
    expect(screen.getByText('名称必填')).toBeInTheDocument();
  });

  it('rejects save when base url has no http scheme', async () => {
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /新建模型配置/ }));
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'n' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'ftp://x' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    expect(createModel).not.toHaveBeenCalled();
    expect(screen.getByText(/必须以 http/)).toBeInTheDocument();
  });

  it('edits an existing config with prefilled values', async () => {
    vi.mocked(updateModel).mockResolvedValue({ ...sample, name: 'Renamed' });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '编辑' }));
    const nameInput = screen.getByLabelText('名称') as HTMLInputElement;
    expect(nameInput.value).toBe('DeepSeek Pro');

    fireEvent.change(nameInput, { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(updateModel).toHaveBeenCalledWith('a1', expect.objectContaining({
        name: 'Renamed', api_key: '',
      })),
    );
  });

  it('deletes a config', async () => {
    vi.mocked(deleteModel).mockResolvedValue(undefined);
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '删除' }));

    await waitFor(() => expect(deleteModel).toHaveBeenCalledWith('a1'));
  });

  it('tests connection for a stored config and shows the result', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: true, latency_ms: 42, error: null });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '测试' }));

    await waitFor(() => expect(screen.getByText(/连接成功（42ms）/)).toBeInTheDocument());
    expect(testModelConnection).toHaveBeenCalledWith({ config_id: 'a1' });
  });

  it('shows failure result for a failed connection test', async () => {
    vi.mocked(testModelConnection).mockResolvedValue({ ok: false, latency_ms: null, error: 'TimeoutError' });
    render(<ModelConfigPage />);
    await waitFor(() => expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: '测试' }));

    await waitFor(() => expect(screen.getByText(/连接失败：TimeoutError/)).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/models/test/ModelConfigPage.test.tsx`
Expected: FAIL（stub 页面没有对应交互）

- [ ] **Step 3: 实现 ModelConfigDialog**

创建 `frontend/src/components/models/ModelConfigDialog.tsx`：

```tsx
import { useEffect, useState } from 'react';
import {
  Button, Dialog, DialogActions, DialogContent, DialogTitle,
  Stack, TextField, Typography,
} from '@mui/material';

import { testModelConnection } from '../../api/client';
import type { ModelConfig, ModelConfigInput, ModelTestResult } from '../../store/types';

interface Props {
  open: boolean;
  initial: ModelConfig | null;
  onClose: () => void;
  onSave: (input: ModelConfigInput) => Promise<void>;
}

export default function ModelConfigDialog({ open, initial, onClose, onSave }: Props) {
  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [modelId, setModelId] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [temperature, setTemperature] = useState('');
  const [strictUrl, setStrictUrl] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [testResult, setTestResult] = useState<ModelTestResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (open) {
      setName(initial?.name ?? '');
      setBaseUrl(initial?.base_url ?? '');
      setModelId(initial?.model_id ?? '');
      setApiKey('');
      setTemperature(initial?.temperature != null ? String(initial.temperature) : '');
      setStrictUrl(initial?.strict_base_url ?? '');
      setShowAdvanced(false);
      setTestResult(null);
      setTouched(false);
    }
  }, [open, initial]);

  const nameError = touched && !name.trim() ? '名称必填' : '';
  const urlError = touched && !/^https?:\/\//.test(baseUrl.trim())
    ? '必须以 http:// 或 https:// 开头'
    : '';
  const modelError = touched && !modelId.trim() ? '模型 ID 必填' : '';
  const hasErrors = Boolean(nameError || urlError || modelError);

  const handleSave = async () => {
    setTouched(true);
    if (hasErrors) return;
    setSaving(true);
    await onSave({
      name: name.trim(),
      base_url: baseUrl.trim(),
      model_id: modelId.trim(),
      api_key: apiKey,
      temperature: temperature.trim() ? Number(temperature) : null,
      strict_base_url: strictUrl.trim() || null,
    });
    setSaving(false);
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      const result = initial
        ? await testModelConnection({ config_id: initial.id, api_key: apiKey })
        : await testModelConnection({
            base_url: baseUrl.trim(), api_key: apiKey, model_id: modelId.trim(),
          });
      setTestResult(result);
    } catch (error) {
      setTestResult({
        ok: false, latency_ms: null,
        error: error instanceof Error ? error.message : String(error),
      });
    }
    setTesting(false);
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{initial ? '编辑模型配置' : '新建模型配置'}</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField
            label="名称"
            value={name}
            onChange={(e) => setName(e.target.value)}
            error={Boolean(nameError)}
            helperText={nameError}
            size="small"
            fullWidth
          />
          <TextField
            label="Base URL"
            placeholder="https://api.deepseek.com/v1"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            error={Boolean(urlError)}
            helperText={urlError}
            size="small"
            fullWidth
          />
          <TextField
            label="模型 ID"
            placeholder="deepseek-v4-flash"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            error={Boolean(modelError)}
            helperText={modelError}
            size="small"
            fullWidth
          />
          <TextField
            label={initial?.has_key ? 'API Key（留空 = 保留原 key）' : 'API Key'}
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            size="small"
            fullWidth
          />
          <Typography
            component="button"
            onClick={() => setShowAdvanced((v) => !v)}
            sx={{ alignSelf: 'flex-start', cursor: 'pointer', bgcolor: 'transparent', border: 'none', color: 'primary.main', p: 0 }}
          >
            {showAdvanced ? '▾ 高级选项' : '▸ 高级选项（可选）'}
          </Typography>
          {showAdvanced && (
            <>
              <TextField
                label="Temperature（可选，0~2）"
                value={temperature}
                onChange={(e) => setTemperature(e.target.value)}
                size="small"
                fullWidth
              />
              <TextField
                label="严格模式地址（可选，留空沿用 .env）"
                value={strictUrl}
                onChange={(e) => setStrictUrl(e.target.value)}
                size="small"
                fullWidth
              />
            </>
          )}
          {testResult && (
            <Typography
              variant="body2"
              color={testResult.ok ? 'success.main' : 'error.main'}
            >
              {testResult.ok
                ? `连接成功（${testResult.latency_ms}ms）`
                : `连接失败：${testResult.error}`}
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button color="inherit" onClick={() => void handleTest()} disabled={testing}>
          测试连接
        </Button>
        <Button color="inherit" onClick={onClose}>取消</Button>
        <Button
          variant="contained"
          disableElevation
          onClick={() => void handleSave()}
          disabled={saving}
        >
          保存
        </Button>
      </DialogActions>
    </Dialog>
  );
}
```

- [ ] **Step 4: 实现 ModelConfigPage**

用以下完整内容替换 `frontend/src/components/models/ModelConfigPage.tsx`：

```tsx
import { useEffect, useState } from 'react';
import { Alert, Box, Button, Container, Stack, Typography } from '@mui/material';

import ModelConfigDialog from './ModelConfigDialog';
import { useModelConfigStore } from '../../store/modelConfigStore';
import { testModelConnection } from '../../api/client';
import type { ModelConfig, ModelConfigInput, ModelTestResult } from '../../store/types';

export default function ModelConfigPage() {
  const { configs, loading, error, load, create, update, remove } = useModelConfigStore();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ModelConfig | null>(null);
  const [testResults, setTestResults] = useState<Record<string, ModelTestResult>>({});

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };

  const openEdit = (config: ModelConfig) => {
    setEditing(config);
    setDialogOpen(true);
  };

  const closeDialog = () => {
    setDialogOpen(false);
    setEditing(null);
  };

  const handleSave = async (input: ModelConfigInput) => {
    const saved = editing ? await update(editing.id, input) : await create(input);
    if (saved) closeDialog();
  };

  const handleTest = async (config: ModelConfig) => {
    try {
      const result = await testModelConnection({ config_id: config.id });
      setTestResults((prev) => ({ ...prev, [config.id]: result }));
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [config.id]: {
          ok: false, latency_ms: null,
          error: err instanceof Error ? err.message : String(err),
        },
      }));
    }
  };

  return (
    <Container maxWidth="md" sx={{ py: 4 }}>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 3 }}>
        <Box>
          <Typography variant="h4" sx={{ fontWeight: 400 }}>模型配置</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            创建游戏时可选；「环境默认 (.env)」始终可用
          </Typography>
        </Box>
        <Button variant="contained" disableElevation onClick={openCreate}>
          新建模型配置
        </Button>
      </Box>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {!loading && configs.length === 0 && (
        <Box sx={{ textAlign: 'center', py: 8 }}>
          <Typography variant="h6" color="text.disabled" sx={{ fontWeight: 400, mb: 1 }}>
            暂无模型配置
          </Typography>
          <Typography variant="body2" color="text.disabled">
            点击「新建模型配置」添加，或直接使用环境默认
          </Typography>
        </Box>
      )}

      <Stack spacing={1.5}>
        {configs.map((config) => {
          const result = testResults[config.id];
          return (
            <Box
              key={config.id}
              sx={{
                bgcolor: 'background.paper',
                border: '1px solid', borderColor: 'divider',
                borderRadius: 2, p: 2,
              }}
            >
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <Box>
                  <Typography variant="subtitle1" sx={{ fontWeight: 500 }}>
                    {config.name}
                    {config.key_invalid && (
                      <Typography component="span" variant="body2" color="warning.main" sx={{ ml: 1 }}>
                        密钥失效，请重输
                      </Typography>
                    )}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    {config.model_id} · {config.base_url}
                    {config.temperature != null ? ` · temp ${config.temperature}` : ''}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    API Key：{config.has_key ? config.api_key_masked : '未设置'}
                  </Typography>
                </Box>
                <Stack direction="row" spacing={1}>
                  <Button size="small" color="inherit" onClick={() => void handleTest(config)}>测试</Button>
                  <Button size="small" color="inherit" onClick={() => openEdit(config)}>编辑</Button>
                  <Button size="small" color="inherit" onClick={() => void remove(config.id)}>删除</Button>
                </Stack>
              </Box>
              {result && (
                result.ok ? (
                  <Alert severity="success" sx={{ mt: 1 }}>连接成功（{result.latency_ms}ms）</Alert>
                ) : (
                  <Alert severity="error" sx={{ mt: 1 }}>连接失败：{result.error}</Alert>
                )
              )}
            </Box>
          );
        })}
      </Stack>

      <ModelConfigDialog
        open={dialogOpen}
        initial={editing}
        onClose={closeDialog}
        onSave={handleSave}
      />
    </Container>
  );
}
```

- [ ] **Step 5: 运行确认通过**

Run: `npx vitest run src/components/models/test/ModelConfigPage.test.tsx`
Expected: PASS

Run: `npm run lint`
Expected: 无错误

- [ ] **Step 6: Commit（分两个 commit）**

```bash
git add frontend/src/components/models/ModelConfigDialog.tsx frontend/src/components/models/ModelConfigPage.tsx
git commit -m "feat(models): add model config list page with edit dialog"

git add frontend/src/components/models/test/ModelConfigPage.test.tsx
git commit -m "test(models): cover model config page interactions"
```

---

### Task 5: 两步创建向导

**Files:**
- Create: `frontend/src/components/create/RoleStep.tsx`
- Create: `frontend/src/components/create/ModelStep.tsx`
- Modify: `frontend/src/components/create/CreateGameWizard.tsx`（替换 stub）
- Test: `frontend/src/components/create/test/CreateGameWizard.test.tsx`

- [ ] **Step 1: 写失败测试**

创建 `frontend/src/components/create/test/CreateGameWizard.test.tsx`：

```tsx
// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import CreateGameWizard from '../CreateGameWizard';
import {
  createGame, createModel, fetchConstraints, fetchPresets, fetchRoleCatalog,
  listModels,
} from '../../../api/client';

vi.mock('../../../api/client', () => ({
  createGame: vi.fn(),
  listModels: vi.fn(),
  createModel: vi.fn(),
  updateModel: vi.fn(),
  deleteModel: vi.fn(),
  testModelConnection: vi.fn(),
  fetchRoleCatalog: vi.fn(),
  fetchPresets: vi.fn(),
  fetchConstraints: vi.fn(),
}));

const navigateMock = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigateMock };
});

const ROLES = [
  { role_id: 'wolf-killer-werewolf', display_name: 'Werewolf', name_zh: '狼人', camp: 'werewolf', icon: 'wolf', description: '', min_count: 0, max_count: null, dependencies: [], exclusions: [] },
  { role_id: 'wolf-killer-villager', display_name: 'Villager', name_zh: '平民', camp: 'good', icon: 'villager', description: '', min_count: 0, max_count: null, dependencies: [], exclusions: [] },
  { role_id: 'wolf-killer-seer', display_name: 'Seer', name_zh: '预言家', camp: 'good', icon: 'seer', description: '', min_count: 0, max_count: null, dependencies: [], exclusions: [] },
  { role_id: 'wolf-killer-witch', display_name: 'Witch', name_zh: '女巫', camp: 'good', icon: 'witch', description: '', min_count: 0, max_count: null, dependencies: [], exclusions: [] },
  { role_id: 'wolf-killer-hunter', display_name: 'Hunter', name_zh: '猎人', camp: 'good', icon: 'hunter', description: '', min_count: 0, max_count: null, dependencies: [], exclusions: [] },
  { role_id: 'wolf-killer-guard', display_name: 'Guard', name_zh: '守卫', camp: 'good', icon: 'guard', description: '', min_count: 0, max_count: null, dependencies: [], exclusions: [] },
];

const PRESETS = [
  { id: 'nine-player-standard', name: '九人标准场', description: '3狼 3民 1预言家 1女巫 1猎人', role_counts: { 'wolf-killer-werewolf': 3, 'wolf-killer-villager': 3, 'wolf-killer-seer': 1, 'wolf-killer-witch': 1, 'wolf-killer-hunter': 1 } },
  { id: 'ten-player-standard', name: '十人标准场', description: '含守卫', role_counts: { 'wolf-killer-werewolf': 3, 'wolf-killer-villager': 3, 'wolf-killer-seer': 1, 'wolf-killer-witch': 1, 'wolf-killer-hunter': 1, 'wolf-killer-guard': 1 } },
];

const CONSTRAINTS = { min_players: 4, max_players: 12, min_werewolves: 1, min_good: 1 };

const MODEL = {
  id: 'm1', name: 'DeepSeek Pro', base_url: 'https://api.deepseek.com/v1',
  model_id: 'deepseek-v4-pro', has_key: true, api_key_masked: 'sk-***1234',
  key_invalid: false, temperature: null, strict_base_url: null,
  created_at: '', updated_at: '',
};

beforeEach(() => {
  vi.mocked(fetchPresets).mockResolvedValue(PRESETS);
  vi.mocked(fetchRoleCatalog).mockResolvedValue(ROLES);
  vi.mocked(fetchConstraints).mockResolvedValue(CONSTRAINTS);
  vi.mocked(listModels).mockResolvedValue([MODEL]);
  vi.mocked(createGame).mockResolvedValue({ game_id: 'g1' });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

async function renderStep1() {
  render(<CreateGameWizard />);
  await waitFor(() => expect(screen.getByText('九人标准场')).toBeInTheDocument());
}

describe('CreateGameWizard step 1', () => {
  it('loads presets, roles, and defaults to the nine-player preset', async () => {
    await renderStep1();
    expect(screen.getByText(/共 9 人/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '下一步' })).toBeEnabled();
  });

  it('adjusting a role switches to custom preset and updates the total', async () => {
    await renderStep1();
    fireEvent.click(screen.getByRole('button', { name: '增加狼人' }));
    expect(screen.getByText(/共 10 人/)).toBeInTheDocument();
  });

  it('stops decrementing wolves at the min_werewolves constraint', async () => {
    vi.mocked(fetchConstraints).mockResolvedValue({ ...CONSTRAINTS, min_players: 9 });
    await renderStep1();
    const minusWolf = screen.getByRole('button', { name: '减少狼人' });
    fireEvent.click(minusWolf);
    fireEvent.click(minusWolf);
    expect(minusWolf).toBeDisabled();
  });

  it('stops decrementing when total would drop below min_players', async () => {
    vi.mocked(fetchConstraints).mockResolvedValue({ ...CONSTRAINTS, min_players: 9 });
    await renderStep1();
    expect(screen.getByRole('button', { name: '减少平民' })).toBeDisabled();
  });

  it('selecting the ten-player preset applies its role counts', async () => {
    await renderStep1();
    fireEvent.click(screen.getByText('十人标准场'));
    expect(screen.getByText(/共 10 人/)).toBeInTheDocument();
  });
});

describe('CreateGameWizard step 2 and submission', () => {
  async function goToStep2() {
    await renderStep1();
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    await waitFor(() => expect(screen.getByText('环境默认 (.env)')).toBeInTheDocument());
  }

  it('shows the env default card and stored configs', async () => {
    await goToStep2();
    expect(screen.getByText('DeepSeek Pro')).toBeInTheDocument();
    expect(screen.getByText('当场新建模型配置')).toBeInTheDocument();
  });

  it('creates the game with the env default and navigates', async () => {
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));

    await waitFor(() =>
      expect(createGame).toHaveBeenCalledWith({
        role_counts: expect.objectContaining({ 'wolf-killer-werewolf': 3 }),
        model_assignments: [{ config_id: null, count: 9 }],
      }),
    );
    expect(navigateMock).toHaveBeenCalledWith('/game/g1');
  });

  it('selects a stored config and submits its id', async () => {
    await goToStep2();
    fireEvent.click(screen.getByText('DeepSeek Pro'));
    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));

    await waitFor(() =>
      expect(createGame).toHaveBeenCalledWith(expect.objectContaining({
        model_assignments: [{ config_id: 'm1', count: 9 }],
      })),
    );
  });

  it('creates a model config inline and selects it', async () => {
    vi.mocked(createModel).mockResolvedValue({ ...MODEL, id: 'm2', name: 'New Model' });
    await goToStep2();
    fireEvent.click(screen.getByText('当场新建模型配置'));

    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'New Model' } });
    fireEvent.change(screen.getByLabelText('Base URL'), { target: { value: 'https://x/v1' } });
    fireEvent.change(screen.getByLabelText('模型 ID'), { target: { value: 'm' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() =>
      expect(createModel).toHaveBeenCalledWith(expect.objectContaining({ name: 'New Model' })),
    );
  });

  it('shows an error alert when creation fails', async () => {
    vi.mocked(createGame).mockRejectedValue(new Error('boom'));
    await goToStep2();
    fireEvent.click(screen.getByRole('button', { name: '创建游戏' }));

    await waitFor(() => expect(screen.getByText('boom')).toBeInTheDocument());
    expect(navigateMock).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `npx vitest run src/components/create/test/CreateGameWizard.test.tsx`
Expected: FAIL（stub 向导无交互）

- [ ] **Step 3: 实现 RoleStep**

创建 `frontend/src/components/create/RoleStep.tsx`：

```tsx
import { useEffect, useState } from 'react';
import { Box, Chip, IconButton, Stack, Typography } from '@mui/material';
import AddIcon from '@mui/icons-material/Add';
import RemoveIcon from '@mui/icons-material/Remove';

import { fetchConstraints, fetchPresets, fetchRoleCatalog } from '../../api/client';
import type { FieldConstraints, GamePreset, RoleCatalogItem } from '../../store/types';

const CUSTOM_PRESET_ID = 'custom';

interface Props {
  roleCounts: Record<string, number>;
  onRoleCountsChange: (counts: Record<string, number>) => void;
  onConstraintsChange: (constraints: FieldConstraints) => void;
}

export default function RoleStep({
  roleCounts, onRoleCountsChange, onConstraintsChange,
}: Props) {
  const [presets, setPresets] = useState<GamePreset[]>([]);
  const [roles, setRoles] = useState<RoleCatalogItem[]>([]);
  const [constraints, setConstraints] = useState<FieldConstraints | null>(null);
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void Promise.all([fetchPresets(), fetchRoleCatalog(), fetchConstraints()])
      .then(([presetList, roleList, constraintValues]) => {
        if (!active) return;
        setPresets(presetList);
        setRoles(roleList);
        setConstraints(constraintValues);
        onConstraintsChange(constraintValues);
        const nine = presetList.find((p) => p.id === 'nine-player-standard') ?? presetList[0];
        if (nine && Object.keys(roleCounts).length === 0) {
          setSelectedPresetId(nine.id);
          onRoleCountsChange({ ...nine.role_counts });
        }
      })
      .catch((error) => {
        if (active) setLoadError(error instanceof Error ? error.message : String(error));
      });
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const total = Object.values(roleCounts).reduce((sum, n) => sum + n, 0);
  const goodCount = roles
    .filter((role) => role.camp === 'good')
    .reduce((sum, role) => sum + (roleCounts[role.role_id] ?? 0), 0);

  const selectPreset = (preset: GamePreset) => {
    setSelectedPresetId(preset.id);
    onRoleCountsChange({ ...preset.role_counts });
  };

  const canIncrement = (role: RoleCatalogItem) => {
    const current = roleCounts[role.role_id] ?? 0;
    if (role.max_count !== null && current >= role.max_count) return false;
    if (!constraints) return true;
    return total < constraints.max_players;
  };

  const canDecrement = (role: RoleCatalogItem) => {
    const current = roleCounts[role.role_id] ?? 0;
    if (current <= 0 || current <= role.min_count) return false;
    if (!constraints) return true;
    if (total - 1 < constraints.min_players) return false;
    if (role.role_id === 'wolf-killer-werewolf' && current - 1 < constraints.min_werewolves) return false;
    if (role.camp === 'good' && goodCount - 1 < constraints.min_good) return false;
    return true;
  };

  const adjust = (role: RoleCatalogItem, delta: number) => {
    const next = {
      ...roleCounts,
      [role.role_id]: Math.max(0, (roleCounts[role.role_id] ?? 0) + delta),
    };
    setSelectedPresetId(CUSTOM_PRESET_ID);
    onRoleCountsChange(next);
  };

  return (
    <Box>
      {loadError && <Typography variant="body2" color="error">{loadError}</Typography>}

      <Typography variant="subtitle2" sx={{ mb: 1 }}>选择标准场或自定义</Typography>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} sx={{ mb: 3 }}>
        {presets.map((preset) => (
          <Box
            key={preset.id}
            onClick={() => selectPreset(preset)}
            sx={{
              flex: 1, cursor: 'pointer', p: 1.5, borderRadius: 2,
              border: '1px solid',
              borderColor: selectedPresetId === preset.id ? 'primary.main' : 'divider',
              bgcolor: 'background.paper',
            }}
          >
            <Typography variant="subtitle2">{preset.name}</Typography>
            <Typography variant="body2" color="text.secondary">{preset.description}</Typography>
          </Box>
        ))}
      </Stack>

      <Typography variant="subtitle2" sx={{ mb: 1 }}>角色配置（点加减即切换为自定义场）</Typography>
      <Stack spacing={1}>
        {roles.map((role) => {
          const count = roleCounts[role.role_id] ?? 0;
          return (
            <Box
              key={role.role_id}
              sx={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                bgcolor: 'background.paper', border: '1px solid',
                borderColor: 'divider', borderRadius: 2, p: 1.5,
              }}
            >
              <Box>
                <Typography variant="body1">{role.name_zh}</Typography>
                <Typography variant="body2" color="text.secondary">{role.description}</Typography>
              </Box>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Chip
                  size="small"
                  variant="outlined"
                  color={role.camp === 'werewolf' ? 'error' : 'default'}
                  label={role.camp === 'werewolf' ? '狼人阵营' : role.camp === 'good' ? '好人阵营' : role.camp}
                />
                <IconButton
                  size="small"
                  aria-label={`减少${role.name_zh}`}
                  disabled={!canDecrement(role)}
                  onClick={() => adjust(role, -1)}
                >
                  <RemoveIcon />
                </IconButton>
                <Typography sx={{ width: 24, textAlign: 'center' }}>{count}</Typography>
                <IconButton
                  size="small"
                  aria-label={`增加${role.name_zh}`}
                  disabled={!canIncrement(role)}
                  onClick={() => adjust(role, 1)}
                >
                  <AddIcon />
                </IconButton>
              </Box>
            </Box>
          );
        })}
      </Stack>

      <Typography variant="body2" color="text.secondary" sx={{ mt: 2 }}>
        共 {total} 人
      </Typography>
    </Box>
  );
}
```

- [ ] **Step 4: 实现 ModelStep**

创建 `frontend/src/components/create/ModelStep.tsx`：

```tsx
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

      <ModelConfigDialog
        open={dialogOpen}
        initial={null}
        onClose={() => setDialogOpen(false)}
        onSave={handleSaved}
      />
    </Box>
  );
}
```

- [ ] **Step 5: 实现 CreateGameWizard**

用以下完整内容替换 `frontend/src/components/create/CreateGameWizard.tsx`：

```tsx
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
```

- [ ] **Step 6: 运行确认通过**

Run: `npx vitest run src/components/create/test/CreateGameWizard.test.tsx src/components/models/test/ModelConfigPage.test.tsx`
Expected: PASS（若测试对 `getByText('环境默认 (.env)')` 匹配失败，改用 `getByText(/环境默认/)`）

Run: `npm run lint`
Expected: 无错误

- [ ] **Step 7: Commit（分两个 commit）**

```bash
git add frontend/src/components/create/RoleStep.tsx frontend/src/components/create/ModelStep.tsx frontend/src/components/create/CreateGameWizard.tsx
git commit -m "feat(create): add two-step game creation wizard"

git add frontend/src/components/create/test/CreateGameWizard.test.tsx
git commit -m "test(create): cover wizard presets, constraints, and submission"
```

---

### Task 6: 全量门禁与收尾

**Files:** 无新增（仅验证与修复）

- [ ] **Step 1: 全量测试**

Run: `npm test`
Expected: 全部通过（存量 57 个 + 新增测试）

- [ ] **Step 2: Lint 与构建**

Run: `npm run lint`
Expected: 无错误

Run: `npm run build`
Expected: `tsc -b && vite build` 成功（类型全通过）

- [ ] **Step 3: 手工冒烟（需后端已按后端计划完成并启动）**

```bash
cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload   # 终端 1
cd frontend && npm run dev                                                  # 终端 2
```

浏览器验证清单：
1. `http://localhost:5173/models` 新建配置（填 DeepSeek url/model/key）→ 列表出现、key 显示 `sk-***xxxx`
2. 点「测试」→ 连接成功提示（失败也显示结构化错误）
3. `http://localhost:5173/create` → 默认九人场共 9 人 → 加守卫变 10 人 → 下一步
4. 第 2 步选环境默认 → 创建游戏 → 跳转对局页且正常开局
5. 第 2 步选刚建的配置 → 创建游戏正常
6. 刷新后 `http://localhost:5173/models` 配置仍在（落盘验证）

- [ ] **Step 4: 修复后提交（如有）**

若冒烟发现缺陷，先补失败测试再修复，按此前 commit 规范提交（≤3 文件/commit）。

---

## Self-Review（已完成）

1. **规格覆盖**：react-router 四路由（Task 1）✅、模型管理页/对话框/测试连接（Task 4）✅、两步向导/预设/角色加减/硬约束禁用（Task 5）✅、环境默认兜底卡片置顶（Task 5 ModelStep）✅、当场新建模型配置（Task 5）✅、`model_assignments` 一期单条 count=总人数（Task 5 handleCreate）✅、类型与 API client（Task 2）✅、store 状态（Task 3）✅。
2. **占位符扫描**：无 TBD/TODO；stub 页面在 Task 4/5 被完整替换，无残留占位。
3. **类型一致性**：`ModelConfigInput` 字段在 Dialog/store/client 一致；`ModelAssignment.config_id: string | null` 与 `selectedModelId`（`string | null`，null=环境默认）语义一致；`role_counts` 键与后端 role_id 一致；测试中 `count: 9` 与九人场总数一致。
4. **注意**：Task 1 的 App.test.tsx 中 `vi.mock('react-router-dom')` 必须在文件顶部声明（提升执行），已用顶部 mock + `navigateMock` 写法修正。
