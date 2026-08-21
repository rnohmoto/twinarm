import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { FfModeSwitch } from './FfModeSwitch'

describe('FfModeSwitch', () => {
  it('sends a mode command when a mode is picked', async () => {
    const onCommand = vi.fn()
    const user = userEvent.setup()
    render(<FfModeSwitch current="off" onCommand={onCommand} />)

    await user.click(screen.getByRole('button', { name: 'arm' }))

    expect(onCommand).toHaveBeenCalledWith({ mode: 'arm' })
  })

  it('marks the mode the arms report as pressed', () => {
    render(<FfModeSwitch current="gripper" onCommand={vi.fn()} />)

    expect(screen.getByRole('button', { name: 'gripper' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    expect(screen.getByRole('button', { name: 'off' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })
})
