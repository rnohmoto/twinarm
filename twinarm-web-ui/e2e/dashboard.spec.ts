import { expect, test } from '@playwright/test'

// The backend is MSW. These tests prove the UI wires up against the contract,
// not that any arm behaves a certain way.
test('renders mocked telemetry and switches force-feedback mode', async ({
  page,
}) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'TwinArm' })).toBeVisible()
  await expect(page.getByRole('cell', { name: 'shoulder_pan' })).toBeVisible()
  await expect(page.getByTestId('ff-mode')).toHaveText('off')

  await page.getByRole('button', { name: 'arm' }).click()

  // The mock backend keeps the mode, so the change comes back through /stream.
  await expect(page.getByTestId('ff-mode')).toHaveText('arm')
})
