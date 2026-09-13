import { configDefaults, defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    exclude: [...configDefaults.exclude, 'e2e/**'],
    coverage: {
      provider: 'v8',
      reporter: ['text'],
      include: [
        'src/store/gameStore.ts',
        'src/store/benchmarkStore.ts',
        'src/api/websocket.ts',
        'src/components/game/GameBoard.tsx',
        'src/components/game/TimelineController.tsx',
        'src/components/game/HistoryPanel.tsx',
        'src/components/game/WinOverlay.tsx',
        'src/components/lobby/GameList.tsx',
        'src/components/lobby/GameCard.tsx',
      ],
      exclude: ['src/**/test/**', 'src/**/*.test.{ts,tsx}', 'src/**/*.spec.{ts,tsx}'],
      thresholds: {
        statements: 100,
        branches: 100,
        functions: 100,
        lines: 100,
      },
    },
  },
})
