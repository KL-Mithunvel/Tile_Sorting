# documents/electrical/

Electrical and control wiring documentation (solenoid driver circuit, sensor wiring,
power distribution — see `documents/project/project_charter.md` §8 for the electronics
and control architecture, and §17 for electrical safety requirements).

| Folder | Contents |
|---|---|
| `schematics/` | Wiring/connection diagrams, circuit schematics |
| `datasheets/` | Component datasheets (solenoid, driver MOSFET, microphone, ToF sensors, etc.) |
| `panel-layout/` | Control panel / enclosure layout drawings |

Currently empty scaffolding — no electrical work has been produced yet as of this
restructure (2026-07-10). Populate as the control/wiring design progresses.

Any threshold or parameter shown on a schematic that was only validated on prototype
hardware (per `.CLAUDE/CLAUDE.md` Development Rule 3) must say so directly on the
document, not just in code comments.
