# entities

Business objects and how they are displayed: telemetry frames, arms, joints.

An entity may model and render its object; it must not know how the data reaches it. `telemetry`
takes a `TelemetrySource` and does not care whether frames come from SSE, a mock, or a test.
