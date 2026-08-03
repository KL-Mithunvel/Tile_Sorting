# Camera Node

Code for the visual inspection station — first in the physical line
(`project_charter.md` §6.1, `Camera_Sorting_Subsystem.md`, `Automation_Architecture.md`
§5.2). Planned target hardware: an Arduino UNO Q (open option to move to a Raspberry Pi
instead — see `Automation_Architecture.md` §5.2).

Folder layout follows the Arduino App Bricks convention (see `app.yaml`), matching
`acoustic_node/` — see that node's `README.md` for the general pattern and its caveats
about App Lab not yet being verified on real hardware.

## Status: nothing built yet

Everything in this folder (`app.yaml`, `sketch/`, `python/main.py`) is placeholder
scaffolding only, so the App Bricks shape is ready when camera work starts. No image
capture, defect detection, or tile-counting code exists yet — see `TODO.md`
("Camera / visual inspection module").
