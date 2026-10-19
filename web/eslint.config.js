import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: [
      'dist',
      'test-results',
      'playwright-report',
      'src/components/ui/**',
      'src/hooks/use-mobile.ts',
    ],
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2023,
      globals: globals.browser,
    },
  },
  {
    // React rules apply to app code only. Playwright's fixture callback is named `use`,
    // which is not a React hook, so e2e stays out of this block.
    files: ['src/**/*.{ts,tsx}'],
    extends: [reactHooks.configs.flat.recommended, reactRefresh.configs.vite],
  },
  {
    // Views reach the data only through `@/api`. The fixture, the generated schema and the
    // raw client module are off limits to them; tests may build a FixtureClient.
    files: ['src/routes/**/*.{ts,tsx}', 'src/shell/**/*.{ts,tsx}', 'src/components/**/*.{ts,tsx}'],
    ignores: ['**/*.test.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: [
                '@/api/fixture/*', '**/api/fixture/**', '@/api/schema', '**/api/schema', '**/api/schema.*', '@/api/client', '**/api/client', '**/api/client.*',
              ],
              message: 'Import from "@/api" only. The fixture, schema and client modules are internal to the seam.',
            },
          ],
        },
      ],
    },
  },
);
