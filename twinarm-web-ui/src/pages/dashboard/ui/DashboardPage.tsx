import { useMemo } from 'react'
import { TelemetrySummary, useTelemetry } from '@/entities/telemetry'
import { FfModeSwitch } from '@/features/ff-mode'
import {
  EventSourceTelemetrySource,
  type Command,
  sendCommand,
} from '@/shared/api'

/** The one worked example of the conventions: a page composing entity + feature. */
export function DashboardPage() {
  const source = useMemo(() => new EventSourceTelemetrySource(), [])
  const frame = useTelemetry(source)

  const handleCommand = (command: Command) => {
    void sendCommand(command).catch((error: unknown) => {
      console.error('command failed', error)
    })
  }

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-6 p-8">
      <h1 className="text-xl font-semibold">TwinArm</h1>
      <FfModeSwitch current={frame?.mode ?? null} onCommand={handleCommand} />
      <TelemetrySummary frame={frame} />
    </main>
  )
}
