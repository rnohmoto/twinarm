import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import { App } from '@/app'

/** There is no backend yet; in dev the contract is served by MSW. */
async function enableMocking() {
  if (!import.meta.env.DEV) return

  const { worker } = await import('@/shared/api/mocks/browser')
  await worker.start({ onUnhandledRequest: 'bypass' })
}

void enableMocking().then(() => {
  const root = document.getElementById('root')
  if (!root) throw new Error('#root is missing from index.html')

  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
})
