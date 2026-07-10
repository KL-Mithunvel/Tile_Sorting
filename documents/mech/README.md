# documents/mech/

Mechanical engineering artifacts for the tile sorting machine (conveyor, tile alignment,
acoustic tapping station, sorting mechanism, packing system — see
`documents/project/project_charter.md` §7 for the mechanical architecture these
implement).

| Folder | Contents |
|---|---|
| `cad/` | Native CAD source files (SolidWorks `.sldprt`/`.sldasm`, Fusion 360 `.f3d`, STEP/IGES exports for interchange) |
| `drawings/` | Drafted sheets exported as PDF — dimensioned drawings, GD&T, assembly drawings |
| `boms/` | Bills of materials, one file per subsystem or assembly |

Currently empty scaffolding — no CAD/electrical work has been produced yet as of this
restructure (2026-07-10). Populate as parts are designed.

Naming: `<assembly-name>_<rev>.<ext>`, e.g. `solenoid_hammer_mount_r1.sldprt`,
`solenoid_hammer_mount_r1.pdf` for its drawing sheet — keep the CAD source and its
exported drawing under matching names so they're easy to pair up.
