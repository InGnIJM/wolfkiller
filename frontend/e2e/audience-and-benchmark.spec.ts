import { expect, test } from '@playwright/test';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  API_ORIGIN, audienceEvent, completedBenchmark, createLiveGame, createMockState, installMockApi,
} from './mockApi';

test.describe.configure({ mode: 'serial' });

interface RunningApi {
  process: ReturnType<typeof spawn>;
  output: string[];
}

async function startTestApi(dataDir: string): Promise<RunningApi> {
  const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const backendDir = resolve(frontendDir, '../backend');
  const output: string[] = [];
  const apiPort = new URL(API_ORIGIN).port;
  const server = spawn(
    'python',
    [
      '-m', 'uvicorn', 'e2e_app:app',
      '--app-dir', resolve(backendDir, 'tests'),
      '--host', '127.0.0.1', '--port', apiPort,
    ],
    {
      cwd: backendDir,
      env: {
        ...process.env,
        WOLFKILLER_DATA_DIR: dataDir,
        MODEL_CONFIG_PATH: join(dataDir, 'models.json'),
        DEEPSEEK_API_KEY: '',
        LLM_PROVIDER_MAX_RETRIES: '0',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    },
  );
  server.stdout.on('data', (chunk) => output.push(String(chunk)));
  server.stderr.on('data', (chunk) => output.push(String(chunk)));

  await expect.poll(async () => {
    if (server.exitCode !== null) {
      throw new Error(`API exited with ${server.exitCode}:\n${output.join('')}`);
    }
    try {
      const response = await fetch(`${API_ORIGIN}/api/health`);
      return response.ok ? await response.json() : null;
    } catch {
      return null;
    }
  }, { timeout: 120_000, intervals: [250, 500, 1000] }).toEqual({
    status: 'ok',
    service: 'wolf-killer',
  });
  return { process: server, output };
}

async function stopTestApi(api: RunningApi): Promise<void> {
  if (api.process.exitCode !== null) return;
  const exit = once(api.process, 'exit');
  api.process.kill('SIGKILL');
  await Promise.race([
    exit,
    new Promise((resolveWait) => setTimeout(resolveWait, 5000)),
  ]);
}

test('runs lifecycle controls through the real API and manually recovers after process restart', async ({ page }) => {
  test.setTimeout(240_000);
  const dataDir = mkdtempSync(join(tmpdir(), 'wolfkiller-e2e-api-'));
  let api: RunningApi | null = null;
  try {
    api = await startTestApi(dataDir);
    await page.goto('/create');
    await expect(page.getByRole('heading', { name: '创建游戏' })).toBeVisible();
    await page.getByRole('button', { name: '下一步' }).click();
    await expect(page.getByText('已分配 9 / 总人数 9')).toBeVisible();
    await page.getByRole('button', { name: '创建游戏', exact: true }).click();

    await expect(page).toHaveURL(/\/game\/[a-z0-9-]+$/);
    const gameId = new URL(page.url()).pathname.split('/').at(-1);
    expect(gameId).toBeTruthy();
    await expect(page.getByText('上帝视角')).toBeVisible();
    await page.getByRole('button', { name: '返回' }).click();
    await page.getByRole('button', { name: '对局操作' }).click();
    await page.getByRole('menuitem', { name: '暂停执行' }).click();
    await expect(page.getByText('已暂停', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '对局操作' }).click();
    await page.getByRole('menuitem', { name: '继续执行' }).click();
    await expect(page.getByText('运行中', { exact: true })).toBeVisible();

    await stopTestApi(api);
    api = null;
    api = await startTestApi(dataDir);
    await page.reload();
    await expect(page.getByText('已中断', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '对局操作' }).click();
    await page.getByRole('menuitem', { name: '恢复对局' }).click();
    await expect(page.getByText('运行中', { exact: true })).toBeVisible();
  } finally {
    if (api) await stopTestApi(api);
    rmSync(dataDir, { recursive: true, force: true });
  }
});

test('catches up audience events after browser network reconnects', async ({ page, context }) => {
  const state = createMockState();
  const game = createLiveGame('offline-game');
  state.games = [game];
  await installMockApi(page, state);
  await page.goto(`/game/${game.game_id}`);
  const progress = page.getByRole('slider', { name: '回放进度' });
  await expect(progress).toHaveAttribute('aria-valuetext', '第2条，共2条事件');

  state.apiAvailable = false;
  await context.setOffline(true);
  game.events.push(
    audienceEvent(3, 'speech', { player_seat: 2, text: '离线期间事件一', round_number: 1, phase: 'speech' }),
    audienceEvent(4, 'speech', { player_seat: 3, text: '离线期间事件二', round_number: 1, phase: 'speech' }),
  );
  await page.waitForTimeout(3100);
  state.apiAvailable = true;
  await context.setOffline(false);

  await expect(progress).toHaveAttribute('aria-valuetext', '第4条，共4条事件', { timeout: 7000 });
  await expect(page.getByText('离线期间事件二')).toBeVisible();
});

test('keeps the historical cursor stable while live events arrive', async ({ page }) => {
  const state = createMockState();
  const game = createLiveGame('history-game');
  game.events.push(audienceEvent(3, 'speech', { player_seat: 2, text: '第三条事件', round_number: 1, phase: 'speech' }));
  state.games = [game];
  await installMockApi(page, state);
  await page.goto(`/game/${game.game_id}`);
  const progress = page.getByRole('slider', { name: '回放进度' });
  await expect(progress).toHaveAttribute('aria-valuetext', '第3条，共3条事件');

  await page.getByRole('button', { name: '上一个事件' }).click();
  await expect(progress).toHaveAttribute('aria-valuetext', '第2条，共3条事件');
  game.events.push(audienceEvent(4, 'speech', { player_seat: 4, text: '直播新增事件', round_number: 1, phase: 'speech' }));

  await expect(progress).toHaveAttribute('aria-valuetext', '第2条，共4条事件', { timeout: 7000 });
  await page.getByText('回到直播').click();
  await expect(progress).toHaveAttribute('aria-valuetext', '第4条，共4条事件');
  await expect(page.getByText('直播新增事件')).toBeVisible();
});

test('creates and previews a benchmark draft, then starts, pauses, resumes and reads report', async ({ page }) => {
  const state = createMockState();
  await installMockApi(page, state);
  await page.goto('/benchmarks/new');

  await expect(page.getByRole('heading', { name: '新建评测草稿' })).toBeVisible();
  await expect(page.getByText('完整区组局数').locator('..').getByText('16')).toBeVisible();
  await page.getByLabel('参赛模型').click();
  await page.getByRole('option', { name: /测试模型/ }).click();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: '创建草稿' }).click();

  await expect(page).toHaveURL(/\/benchmarks\/benchmark-e2e$/);
  expect(state.lastBenchmarkRequest?.games).toBe(16);
  expect(state.lastBenchmarkRequest?.concurrency).toBe(1);
  await page.getByRole('button', { name: '开始评测' }).click();
  await expect(page.getByText('running', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '暂停', exact: true }).click();
  await expect(page.getByText('paused', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '继续', exact: true }).click();
  await expect(page.getByText('running', { exact: true })).toBeVisible();
  await expect(page.getByText('1.0.0')).toBeVisible();
  await expect(page.getByText('1,200')).toBeVisible();
});

test('opens the failed game at its linked audience event', async ({ page }) => {
  const state = createMockState();
  state.benchmark = completedBenchmark();
  state.benchmarkItems = [{
    run_id: state.benchmark.run_id, item_index: 0, scenario_id: 'four-player-e2e',
    pair_id: null, block_index: 0, assignment: {}, game_id: 'failed-game',
    status: 'failed', terminal_reason: 'model_timeout', event_seq: 2,
  }];
  const game = createLiveGame('failed-game');
  game.events.push(audienceEvent(3, 'speech', { player_seat: 4, text: '失败后的后续事件', round_number: 1, phase: 'speech' }));
  state.games = [game];
  await installMockApi(page, state);
  await page.goto(`/benchmarks/${state.benchmark.run_id}`);

  await page.getByRole('link', { name: '查看回放 · 事件 2' }).click();
  await expect(page).toHaveURL(/\/game\/failed-game\?seq=2$/);
  await expect(page.getByRole('slider', { name: '回放进度' })).toHaveAttribute('aria-valuetext', '第2条，共3条事件');
  await expect(page.getByText('回到直播')).toBeVisible();
});

test('opens a completed legacy archive through detail and full-log compatibility APIs', async ({ page }) => {
  const state = createMockState();
  const game = createLiveGame('legacy-completed');
  game.name = '旧完成存档';
  game.oldArchive = true;
  game.phase = 'game_over';
  game.execution_status = 'completed';
  game.winner = 'good';
  game.events.push(audienceEvent(3, 'winner', { winning_camp: 'good', reason: 'all_wolves_dead' }));
  state.games = [game];
  await installMockApi(page, state);
  await page.goto(`/game/${game.game_id}`);

  await expect(page.getByText('好人阵营获胜')).toBeVisible();
  await page.getByRole('button', { name: '查看对局' }).click();
  await expect(page.getByRole('slider', { name: '回放进度' })).toHaveAttribute('aria-valuetext', '第3条，共3条事件');
  await expect(page.getByText('第1轮 · 已结束')).toBeVisible();
});
