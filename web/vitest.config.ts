import { mergeConfig } from 'vite';
import { defineConfig } from 'vitest/config';
import viteConfig from './vite.config.ts';

export default mergeConfig(
  viteConfig,
  defineConfig({
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['src/test/setup.ts'],
      include: ['src/**/*.test.{ts,tsx}'],
      // GitHub's shared runners are slower than his Mac: four timing-heavy tests passed in 2 to 3 s locally and hit the
      // 5 s default there. CI gets more room; local runs keep the default so a slow test still shows up.
      // Local runs use two workers so a test run never takes every core of his Mac while builders work; CI keeps
      // vitest's default.
      maxWorkers: process.env.CI ? undefined : 2,
      testTimeout: process.env.CI ? 20000 : 5000,
    },
  }),
);
