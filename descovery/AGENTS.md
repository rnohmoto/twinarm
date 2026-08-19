# AGENTS.md — descovery

Hardware experiment sandbox for the Koch v1.1 arms. Repository-wide instructions are in
[`../AGENTS.md`](../AGENTS.md), and the hardware safety rules there apply to everything here.

Read @README.md for the script inventory, each script's hardware risk, and how to run them.

## Rules for this directory

- Check a script's risk class in the inventory before running it. Anything marked *moves motors*,
  *energizes*, *torque off*, or *writes motor config* needs an explicit request from the user in the
  current session: a *torque off* script makes a raised arm drop, and *writes motor config* changes
  EEPROM that then has to be undone by hand.
- The scripts are deliberately standalone and repeat each other's helpers. Do not refactor them into
  shared modules or a package unless asked.
- Nothing is built or published from here; the packaging config is vestigial. Do not rename the
  directory or "fix" the package-name collision with `twinarm/`.
- New experiments belong here as new standalone scripts, not in the `twinarm/` library.
