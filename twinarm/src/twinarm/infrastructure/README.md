# infrastructure — adapters for the domain's ports

Implementations of the ports declared in [`../domain/`](../domain/README.md), backed by real
technology: the lerobot/Dynamixel bus, serial ports. Empty so far — this README records the rules
before the first adapter exists.

## What belongs here

- One module per adapter, named for what it wraps: a future lerobot-backed implementation of the
  domain's bus port, for example. An adapter translates driver vocabulary into domain vocabulary
  at its edge; lerobot types must never pass through a port.
- Repository implementations, once the domain declares repository ports.

## What never belongs here

- Domain rules. If it makes sense with no hardware attached, it belongs in `domain/`.
- Routers, pydantic schemas, or use cases — those are slice property under `api/features/`.
- Connections at import or construction time. Importing this package or constructing an adapter
  must never touch hardware — connecting is an explicit method the caller invokes. This extends
  the package-wide no-hardware-at-import rule to adapters.

## Imports

`infrastructure` imports `domain` (the ports it implements) and its drivers; no slice imports
`infrastructure`. Only `api/app.py` does, binding adapters to ports as FastAPI dependencies, so
slices are tested against fakes of the ports rather than against this package.
