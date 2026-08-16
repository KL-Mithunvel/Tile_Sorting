# Pick-and-Place Node

Code for the Cartesian gantry's machine-control layer (`project_charter.md` §7.4
Decision, `Automation_Architecture.md` §5.6, `documents/requirements/requirements.md`
FR-18). **Newly decided this session: target hardware is an Arduino UNO Q** — previously
"hardware not yet fixed" in the project docs (now updated, see
`Automation_Architecture.md` §5.6).

Folder layout follows the Arduino App Bricks convention (see `app.yaml`), matching
`acoustic_node/` — see that node's `README.md` for the general pattern and its caveats
about App Lab not yet being verified on real hardware.

## Status: nothing built yet

Everything in this folder (`app.yaml`, `sketch/`, `python/main.py`) is placeholder
scaffolding only. The command interface is decided (master sends high-level
grade/slot commands; this layer translates to axis motion — not an off-the-shelf
CNC/G-code controller), but axis count, motor/gripper hardware, and travel limits are
still open — see `TODO.md` ("Design the gantry's custom machine-control layer").

## HMI / digital twin dashboard (design in progress, 2026-08-11)

Full feature sketch for this node's local HMI (control panel, status panel, live digital
twin visualization) is in `documents/project/Pick_Place_Sorting_Subsystem.md`. Current
work: building and testing the digital twin visualization standalone (mock/simulated
position data, no real `GantryWorker` yet) before any motion-control code or full node
integration.
