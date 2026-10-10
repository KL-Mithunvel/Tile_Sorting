# documents/electrical/

Electrical and control wiring documentation (solenoid driver circuit, sensor wiring,
power distribution — see `documents/project/project_charter.md` §8 for the electronics
and control architecture, and §17 for electrical safety requirements).

| Folder | Contents |
|---|---|
| `schematics/` | Wiring/connection diagrams, circuit schematics |
| `datasheets/` | Component datasheets (solenoid, driver MOSFET, microphone, ToF sensors, etc.) |
| `panel-layout/` | Control panel / enclosure layout drawings |

Contents so far (all design documents; **nothing here has been wired or run on hardware**):

| File | What it covers |
|---|---|
| `schematics/acoustic_station_wiring.md` | Acoustic station: ToF-triggered dual-solenoid ball-drop (2026-08-20). Pending revision for the proposed Nano controller (2026-10-10). |
| `schematics/pick_place_hardware_connections_plan.md` | Pick-and-place: CNC Shield V3 + TMC2208 + NEMA 17, pin table, now closed-loop with AS5600 encoders. |
| `network_plan.md` | D-Link DIR-825 control LAN: setup checklist, provisional IP/hostname/port table (2026-10-10). |
| `i2c_bus_plan.md` | The I2C buses: TCA9548A mux, AS5600 encoders, VL53L1X ToF sensors, problems found, bench tests (2026-10-10). |

Any threshold or parameter shown on a schematic that was only validated on prototype
hardware (per `.CLAUDE/CLAUDE.md` Development Rule 3) must say so directly on the
document, not just in code comments.
