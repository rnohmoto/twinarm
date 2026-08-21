# shared

Code with no business meaning of its own, reusable anywhere: the backend contract (`api`),
configuration (`config`), helpers (`lib`), and generic UI primitives (`ui`).

Unlike the other layers, `shared` is organised by segment rather than by slice, and segments are
imported directly (`@/shared/api`). Nothing here may import from a layer above.
