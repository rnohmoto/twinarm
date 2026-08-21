import { z } from 'zod'

/** Joint order used by the arms, leader and follower alike. */
export const JOINTS = [
  'shoulder_pan',
  'shoulder_lift',
  'elbow_flex',
  'wrist_flex',
  'wrist_roll',
  'gripper',
] as const

export const JointSchema = z.enum(JOINTS)
export type Joint = z.infer<typeof JointSchema>

/** Per-joint readings. The prototype omits joints it has no reading for. */
export const JointValuesSchema = z.partialRecord(JointSchema, z.number())
export type JointValues = z.infer<typeof JointValuesSchema>

/** Force-feedback mode. */
export const FfModeSchema = z.enum(['off', 'gripper', 'arm'])
export type FfMode = z.infer<typeof FfModeSchema>

/**
 * One telemetry frame, as pushed over SSE.
 *
 * Currents are milliamps, except `shoulder_pan` and `shoulder_lift`, which the
 * firmware reports as load percent. Positions are normalized, not radians.
 * Unknown keys are kept: the prototype adds fields faster than this schema.
 */
export const TelemetryFrameSchema = z
  .object({
    t: z.number(),
    pos: JointValuesSchema,
    fpos: JointValuesSchema,
    cur: JointValuesSchema,
    ff: z.number(),
    params: z.record(z.string(), z.number()),
    mode: FfModeSchema,
    temp: z.number(),
    n: z.number(),
    rec: z.number(),
    /** Receive wall-clock, added by the panel server rather than the arms. */
    _rx: z.number().optional(),
  })
  .loose()

export type TelemetryFrame = z.infer<typeof TelemetryFrameSchema>
