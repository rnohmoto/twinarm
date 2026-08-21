import { describe, expect, it } from 'vitest'
import { CommandSchema } from './commands'
import { TelemetryFrameSchema } from './telemetry'
import { parseFrame } from './transport'

const frame = {
  t: 12.5,
  pos: { shoulder_pan: 0.1, gripper: -0.3 },
  fpos: { shoulder_pan: 0.09 },
  cur: { elbow_flex: 120 },
  ff: 0,
  params: { ff_gain: 1.2 },
  mode: 'gripper',
  temp: 34,
  n: 190,
  rec: 0,
  _rx: 1_700_000_000.5,
}

describe('TelemetryFrameSchema', () => {
  it('accepts a frame in the shape the panel prototype sends', () => {
    expect(TelemetryFrameSchema.parse(frame).mode).toBe('gripper')
  })

  it('keeps unknown fields so a newer backend does not break the UI', () => {
    const parsed = TelemetryFrameSchema.parse({
      ...frame,
      gripper_closed: true,
    })

    expect(parsed).toHaveProperty('gripper_closed', true)
  })

  it('rejects an unknown force-feedback mode', () => {
    expect(
      TelemetryFrameSchema.safeParse({ ...frame, mode: 'bogus' }).success,
    ).toBe(false)
  })
})

describe('parseFrame', () => {
  it('returns null for payloads that are not valid JSON', () => {
    expect(parseFrame('not json')).toBeNull()
  })

  it('returns null for JSON that does not match the contract', () => {
    expect(parseFrame(JSON.stringify({ t: 1 }))).toBeNull()
  })
})

describe('CommandSchema', () => {
  it('accepts a mode command', () => {
    expect(CommandSchema.parse({ mode: 'arm' })).toEqual({ mode: 'arm' })
  })

  it('accepts a single parameter', () => {
    expect(CommandSchema.parse({ ff_gain: 1.5 })).toEqual({ ff_gain: 1.5 })
  })

  it('rejects an empty command', () => {
    expect(CommandSchema.safeParse({}).success).toBe(false)
  })

  it('rejects two parameters in one command', () => {
    expect(CommandSchema.safeParse({ ff_gain: 1, arm_gain: 1 }).success).toBe(
      false,
    )
  })
})
