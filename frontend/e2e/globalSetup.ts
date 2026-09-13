import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export default async function globalSetup() {
  const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
  const portProbe = createServer();
  portProbe.listen(0, '127.0.0.1');
  await once(portProbe, 'listening');
  const address = portProbe.address();
  if (address === null || typeof address === 'string') throw new Error('Could not reserve an API port');
  const apiOrigin = `http://127.0.0.1:${address.port}`;
  await new Promise<void>((resolveClose, rejectClose) => {
    portProbe.close((error) => error ? rejectClose(error) : resolveClose());
  });
  process.env.WOLFKILLER_E2E_API_ORIGIN = apiOrigin;
  const output: string[] = [];
  const vite = spawn(
    process.execPath,
    ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '4173'],
    {
      cwd: frontendDir,
      env: { ...process.env, VITE_API_URL: apiOrigin },
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: true,
    },
  );
  vite.stdout.on('data', (chunk) => output.push(String(chunk)));
  vite.stderr.on('data', (chunk) => output.push(String(chunk)));

  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    if (vite.exitCode !== null) {
      throw new Error(`Vite exited with ${vite.exitCode}:\n${output.join('')}`);
    }
    try {
      const response = await fetch('http://127.0.0.1:4173');
      if (response.ok) {
        return async () => {
          if (vite.exitCode === null) {
            const exit = once(vite, 'exit');
            vite.kill('SIGKILL');
            await Promise.race([
              exit,
              new Promise((resolveWait) => setTimeout(resolveWait, 5000)),
            ]);
          }
        };
      }
    } catch {
      // The process is still starting.
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 250));
  }

  if (vite.exitCode === null) vite.kill('SIGKILL');
  throw new Error(`Timed out waiting for Vite:\n${output.join('')}`);
}
