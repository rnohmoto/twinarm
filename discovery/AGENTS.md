# AGENTS.md — discovery

Hardware experiment sandbox for the Koch v1.1 arms. Repository-wide instructions are in
[`../AGENTS.md`](../AGENTS.md), and the hardware safety rules there apply to everything here.

Read @README.md for what lives here and the risk classes every inventory uses.

## Rules for this directory

- Check a script's risk class in the inventory before running it. Anything marked *moves motors*,
  *energizes*, *torque off*, or *writes motor config* needs an explicit request from the user in the
  current session: a *torque off* script makes a raised arm drop, and *writes motor config* changes
  EEPROM that then has to be undone by hand.
- `koch/` and `dobot/` are independent uv projects: run `uv` inside them, never here.
  `discovery/pyproject.toml` only relays the shared ruff configuration (it keeps the `twinarm`
  package name; known, leave it).
- Koch work goes into the `koch` package (`koch/src/koch/`, a console script only when an operator
  needs one), with its offline tests; see [`koch/AGENTS.md`](koch/AGENTS.md). Pure control laws
  belong to `twinarm/domain` and are mirrored, not forked.
- `koch/` can be rehearsed with no arms attached: `cd koch && ./start.sh rehearse` (or
  `mise run rehearse`) runs its launch paths against simulated servos and checks them (see
  `koch/simbus/README.md`). Run it, alone, after changing anything under `koch/src/koch/`. A pass
  says the software path holds together, nothing about the arms.
