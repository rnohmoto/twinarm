import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

// Testing Library only auto-cleans when `globals` is on; it is off here, so
// unmount between tests explicitly or renders leak into the next test.
afterEach(cleanup)
