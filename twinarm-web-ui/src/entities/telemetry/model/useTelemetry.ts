import { useEffect, useState } from 'react'
import type { TelemetryFrame, TelemetrySource } from '@/shared/api'

/** Holds the most recent frame from a source; null until the first one arrives. */
export function useTelemetry(source: TelemetrySource): TelemetryFrame | null {
  const [frame, setFrame] = useState<TelemetryFrame | null>(null)

  useEffect(() => source.subscribe(setFrame), [source])

  return frame
}
