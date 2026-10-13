import { defineConfig, globalIgnores } from 'eslint/config'
import nextVitals from 'eslint-config-next/core-web-vitals'
import nextTs from 'eslint-config-next/typescript'

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // A leading underscore names a parameter that is there for its type, as in a typed mock.
      '@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
    },
  },
  globalIgnores(['.next/**', 'out/**', 'test-results/**', 'playwright-report/**', 'next-env.d.ts', 'src/lib/types.generated.ts']),
])
