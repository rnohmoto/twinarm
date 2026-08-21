import type { Command } from './commands'
import { type TelemetryFrame, TelemetryFrameSchema } from './telemetry'

/**
 * Where telemetry frames come from.
 *
 * Everything that renders telemetry takes this interface rather than an
 * `EventSource`, so tests can feed frames in without a network or a mock
 * service worker.
 */
export interface TelemetrySource {
  subscribe(onFrame: (frame: TelemetryFrame) => void): () => void
}

const TELEMETRY_PATH = '/stream'
const COMMAND_PATH = '/ctl'

/** Reads the SSE stream the panel server exposes. Invalid frames are dropped. */
export class EventSourceTelemetrySource implements TelemetrySource {
  readonly url: string

  constructor(url: string = TELEMETRY_PATH) {
    this.url = url
  }

  subscribe(onFrame: (frame: TelemetryFrame) => void): () => void {
    const source = new EventSource(this.url)

    source.onmessage = (event: MessageEvent<string>) => {
      const frame = parseFrame(event.data)
      if (frame) onFrame(frame)
    }

    return () => source.close()
  }
}

/** Parses one SSE payload, returning null when it does not match the contract. */
export function parseFrame(data: string): TelemetryFrame | null {
  let json: unknown
  try {
    json = JSON.parse(data)
  } catch {
    return null
  }

  const result = TelemetryFrameSchema.safeParse(json)
  return result.success ? result.data : null
}

/** Sends one command. The server answers 204 on success and 400 on a malformed body. */
export async function sendCommand(command: Command): Promise<void> {
  const query = encodeURIComponent(JSON.stringify(command))
  const response = await fetch(`${COMMAND_PATH}?c=${query}`)

  if (!response.ok) {
    throw new Error(
      `command rejected: ${response.status} ${response.statusText}`,
    )
  }
}
