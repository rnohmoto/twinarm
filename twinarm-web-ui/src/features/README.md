# features

User actions that change something: sending a command, starting or stopping teleoperation.

A feature receives its command sender as a prop rather than importing a transport, so it can be
tested and reused without a backend. `ff-mode` is the worked example.
