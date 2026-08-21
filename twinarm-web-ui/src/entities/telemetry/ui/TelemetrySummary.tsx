import { JOINTS, type TelemetryFrame } from '@/shared/api'

interface TelemetrySummaryProps {
  frame: TelemetryFrame | null
}

/** Renders the latest frame as a table. Layout only — no polling, no fetching. */
export function TelemetrySummary({ frame }: TelemetrySummaryProps) {
  if (!frame) {
    return <p className="text-sm text-gray-500">Waiting for telemetry…</p>
  }

  return (
    <section className="flex flex-col gap-3">
      <dl className="flex gap-6 text-sm">
        <div>
          <dt className="text-gray-500">Mode</dt>
          <dd data-testid="ff-mode">{frame.mode}</dd>
        </div>
        <div>
          <dt className="text-gray-500">Temp</dt>
          <dd>{frame.temp} °C</dd>
        </div>
        <div>
          <dt className="text-gray-500">Frames</dt>
          <dd>{frame.n}</dd>
        </div>
        <div>
          <dt className="text-gray-500">Reconnects</dt>
          <dd>{frame.rec}</dd>
        </div>
      </dl>

      <table className="text-left text-sm tabular-nums">
        <thead className="text-gray-500">
          <tr>
            <th className="pr-6 font-normal">Joint</th>
            <th className="pr-6 font-normal">Leader</th>
            <th className="pr-6 font-normal">Follower</th>
            <th className="font-normal">Current</th>
          </tr>
        </thead>
        <tbody>
          {JOINTS.map((joint) => (
            <tr key={joint}>
              <td className="pr-6">{joint}</td>
              <td className="pr-6">{format(frame.pos[joint])}</td>
              <td className="pr-6">{format(frame.fpos[joint])}</td>
              <td>{format(frame.cur[joint])}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function format(value: number | undefined): string {
  return value === undefined ? '—' : value.toFixed(2)
}
