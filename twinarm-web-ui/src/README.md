# Source layout — Feature-Sliced Design

The code is organised by [Feature-Sliced Design](https://feature-sliced.design/). Two rules matter:

1. **Layers import downwards only.** The order is
   `app → pages → widgets → features → entities → shared`. A layer may import from the layers below
   it and never from the layers above or from a sibling slice in the same layer.
2. **Slices are consumed through their public API.** Import `@/features/ff-mode`, never
   `@/features/ff-mode/ui/FfModeSwitch`. Each slice re-exports what it offers from its `index.ts`.

`mise run fsd` enforces both mechanically. Every layer directory exists even when it is empty, so
there is never a question of where a new file goes.
