import { http, HttpResponse } from 'msw'
import { CommandSchema } from '../commands'
import {
  JOINTS,
  type FfMode,
  type JointValues,
  type TelemetryFrame,
} from '../telemetry'

const FRAME_INTERVAL_MS = 66 // ~15 Hz, matching the prototype panel
const MOCK_TEMP_C = 34

/**
 * State the mock backend keeps between requests, so a command sent through
 * `/ctl` shows up in the `/stream` frames that follow.
 */
const state = { mode: 'off' as FfMode, frame: 0 }

export const handlers = [
  http.get('/stream', () => {
    let timer: ReturnType<typeof setInterval>

    const stream = new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder()
        timer = setInterval(() => {
          const payload = JSON.stringify(nextFrame())
          controller.enqueue(encoder.encode(`data: ${payload}\n\n`))
        }, FRAME_INTERVAL_MS)
      },
      cancel() {
        clearInterval(timer)
      },
    })

    return new HttpResponse(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
      },
    })
  }),

  http.get('/ctl', ({ request }) => {
    const raw = new URL(request.url).searchParams.get('c')
    if (!raw) return new HttpResponse(null, { status: 400 })

    let parsed: unknown
    try {
      parsed = JSON.parse(raw)
    } catch {
      return new HttpResponse(null, { status: 400 })
    }

    const command = CommandSchema.safeParse(parsed)
    if (!command.success) return new HttpResponse(null, { status: 400 })

    if ('mode' in command.data) state.mode = command.data.mode

    return new HttpResponse(null, { status: 204 })
  }),
]

function nextFrame(): TelemetryFrame {
  const t = (state.frame += 1) * (FRAME_INTERVAL_MS / 1000)

  return {
    t,
    pos: wave(t, 0),
    fpos: wave(t, 0.15),
    cur: wave(t, 0, 120),
    ff: 0,
    params: {
      ff_gain: 1,
      ff_cap: 300,
      ff_floor: 20,
      arm_gain: 0.5,
      arm_cap: 200,
      max_rel: 40,
    },
    mode: state.mode,
    temp: MOCK_TEMP_C,
    n: state.frame,
    rec: 0,
  }
}

/** Plausible-looking motion so charts and tables have something to render. */
function wave(t: number, lag: number, scale = 1): JointValues {
  return Object.fromEntries(
    JOINTS.map((joint, index) => [joint, Math.sin(t - lag + index) * scale]),
  ) as JointValues
}
