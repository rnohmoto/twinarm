import { z } from 'zod'
import { FfModeSchema } from './telemetry'

/** A tunable the panel exposes as a slider. Ranges come from the prototype. */
export interface ParamSpec {
  readonly key: ParamKey
  readonly label: string
  readonly min: number
  readonly max: number
  readonly step: number
}

export const PARAM_KEYS = [
  'ff_gain',
  'ff_cap',
  'ff_floor',
  'arm_gain',
  'arm_cap',
  'max_rel',
] as const

export const ParamKeySchema = z.enum(PARAM_KEYS)
export type ParamKey = z.infer<typeof ParamKeySchema>

export const PARAM_SPECS: readonly ParamSpec[] = [
  { key: 'ff_gain', label: 'Grip gain', min: 0, max: 2.5, step: 0.1 },
  { key: 'ff_cap', label: 'Grip cap (mA)', min: 60, max: 900, step: 10 },
  { key: 'ff_floor', label: 'Return spring (mA)', min: 0, max: 120, step: 5 },
  { key: 'arm_gain', label: 'Arm gain', min: 0, max: 1.5, step: 0.05 },
  { key: 'arm_cap', label: 'Arm cap (mA)', min: 0, max: 400, step: 10 },
  { key: 'max_rel', label: 'Tracking limiter', min: 5, max: 100, step: 5 },
]

/**
 * A command carries exactly one key — the teleop loop reads one field per
 * datagram and ignores the rest.
 */
export const CommandSchema = z.union([
  z.strictObject({ mode: FfModeSchema }),
  z.strictObject({ resync: z.literal(1) }),
  z.strictObject({ stop: z.literal(1) }),
  z
    .partialRecord(ParamKeySchema, z.number())
    .refine((value) => Object.keys(value).length === 1, {
      message: 'a parameter command sets exactly one parameter',
    }),
])

export type Command = z.infer<typeof CommandSchema>
