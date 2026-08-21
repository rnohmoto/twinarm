# shared/api — the backend contract (PROVISIONAL)

**There is no backend.** This layer describes one that does not exist yet, so that the UI can be
built and tested with no arms attached. Treat every shape here as provisional.

The contract is derived from the working prototype
[`../../../../descovery/koch_web_panel.py`](../../../../descovery/koch_web_panel.py), a stdlib-only
panel that the teleop loop launches:

|           |                                                                               |
| --------- | ----------------------------------------------------------------------------- |
| Telemetry | `GET /stream` — Server-Sent Events, one JSON frame per event, ~15 Hz          |
| Commands  | `GET /ctl?c=<url-encoded JSON>` — `204` on success, `400` on a malformed body |

Units follow the prototype and are not SI: positions are normalized, currents are milliamps except
`shoulder_pan` and `shoulder_lift`, which report load percent.

## Files

- `telemetry.ts` — frame schema and joint constants
- `commands.ts` — command union and the tunable parameter table
- `transport.ts` — the `TelemetrySource` interface, the SSE implementation, and `sendCommand`
- `mocks/` — MSW handlers implementing the contract above; browser-only (dev server and Playwright)

## Rules

- Schemas are zod, and types are derived with `z.infer`. Do not hand-write a type that duplicates a
  schema.
- Frames are validated at the boundary. `parseFrame` drops anything that does not match rather than
  letting it reach a component.
- The frame schema is `.loose()`: a backend that adds fields must not break the UI.
- When a real backend arrives, reconcile it here first. This is the only place the wire format is
  written down.
