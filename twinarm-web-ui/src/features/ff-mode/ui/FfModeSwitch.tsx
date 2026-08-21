import { type Command, type FfMode, FfModeSchema } from '@/shared/api'

interface FfModeSwitchProps {
  /** The mode the arms report, not local state — the arms are the source of truth. */
  current: FfMode | null
  /** Injected so tests and stories can observe commands without a network. */
  onCommand: (command: Command) => void
}

/** Switches the force-feedback mode. */
export function FfModeSwitch({ current, onCommand }: FfModeSwitchProps) {
  return (
    <div className="flex gap-2" role="group" aria-label="Force feedback mode">
      {FfModeSchema.options.map((mode) => (
        <button
          key={mode}
          type="button"
          aria-pressed={mode === current}
          onClick={() => onCommand({ mode })}
          className={
            mode === current
              ? 'rounded border border-gray-900 bg-gray-900 px-3 py-1 text-sm text-white'
              : 'rounded border border-gray-300 px-3 py-1 text-sm'
          }
        >
          {mode}
        </button>
      ))}
    </div>
  )
}
