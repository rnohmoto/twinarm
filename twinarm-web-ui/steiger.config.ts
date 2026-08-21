import { defineConfig } from 'steiger'
import fsd from '@feature-sliced/steiger-plugin'

export default defineConfig([
  ...fsd.configs.recommended,
  {
    // The repository ships exactly one worked example per layer, so every slice
    // is referenced once by the dashboard page. That is the point of the
    // example, not a sign the slices should be merged. Re-enable this rule once
    // there is a second page.
    rules: {
      'fsd/insignificant-slice': 'off',
    },
  },
])
