# Management platform reference

This local manager is a contract-focused reimplementation of the task
distribution platform supplied as:

- archive: `sv-task-management-system-source-56e8f27.zip`
- upstream snapshot: `56e8f278dc219837d80ce32ea984472a29ff13d6`
- archive SHA-256: `fe5e33da04ea8fdc5027971c88156f4f66d5d2d3592fedb28d8c4ba81b56f180`

The reference platform's dense operational layout, expert/domain model, and
local SQLite workflow informed this UI. Its catalog startup path deleted all
tasks when a hard-coded catalog version changed, and its local and production
servers duplicated workflow logic. Those behaviors are intentionally not
copied. The CLI, loopback UI, and authenticated gateway now share one SQLite
core. Frozen per-expert task bundles are activated atomically and served
through one shared viewer runtime, so deployment URLs and runtime upgrades do
not alter bundle identity.
