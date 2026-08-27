# domain — the teleoperation model

The domain layer of TwinArm's one bounded context: teleoperation. Empty so far — this README
records what will live here and what never will, so the rules exist before the first file does.

## What belongs here

Plain Python organised by concept: a future `arm.py` would hold the arm's value objects, entities,
and the ports that serve them, side by side. There are no `entities.py` / `value_objects.py` /
`ports.py` buckets.

- **Value objects** — frozen dataclasses compared by value: a normalized joint position, a current
  cap.
- **Entities and aggregates** — objects with identity whose methods enforce their own invariants.
  Behavior belongs on the model; a class of bare fields with its rules kept elsewhere is the
  anemic model this layer exists to avoid.
- **Domain services** — teleoperation operations that fit no single entity.
- **Ports** — `typing.Protocol` interfaces for what the domain needs from the outside world (a
  motor bus, a telemetry sink), owned here and implemented in
  [`../infrastructure/`](../infrastructure/README.md). Repositories are ports too. A port lives in
  the module of the concept it serves, never in a bucket.

## What never belongs here

- Imports beyond the stdlib: no FastAPI, no pydantic, no lerobot. Living outside `api/` also keeps
  the path-scoped FastAPI rules away from these files.
- Wire shapes. The `/stream` and `/ctl` JSON contract is owned by
  [`twinarm-web-ui/src/shared/api/`](../../../../twinarm-web-ui/src/shared/api/README.md); the
  pydantic schemas in each slice translate wire ↔ domain, so those shapes never leak in.
- I/O of any kind. Nothing here opens a serial port, sleeps, or serves a request.

## Ubiquitous language

Speak teleoperation, never HTTP or lerobot: leader arm and follower arm; joint (`shoulder_pan`,
`shoulder_lift`, `elbow_flex`, `wrist_flex`, `wrist_roll`, `gripper`); normalized position;
current in milliamps, with the two shoulder joints reporting load percent; force-feedback mode
(`off`, `gripper`, `arm`); telemetry frame; tunable parameter. The vocabulary comes from the
working prototype [`descovery/mock/v0/koch_web_panel.py`](../../../../descovery/mock/v0/koch_web_panel.py) and the
contract derived from it — grow it from there rather than inventing terms ahead of working code.

## Imports

`api/features/* → domain ← infrastructure`. Anything may import `domain`; `domain` imports nothing
of the project's. Only `api/app.py` sees both sides, binding infrastructure adapters to domain
ports as FastAPI dependencies. This mirrors the web UI's layers-import-downwards rule in
[`twinarm-web-ui/src/README.md`](../../../../twinarm-web-ui/src/README.md).

## Growth

One bounded context means one flat package — a `contexts/` level appears only if a second bounded
context ever does. Use cases are not domain: they stay slice-local under `api/features/`; when a
second slice needs the same behavior, that is usually the moment it turns out to be domain and
moves here.
