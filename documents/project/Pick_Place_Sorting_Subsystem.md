# Pick-and-Place Node — HMI / Digital Twin Dashboard

Design notes for the pick-and-place station's local HMI (charter §14 Monitoring
Architecture, requirements.md FR-19), sketched out 2026-08-11 ahead of any motion-control
code. Mirrors the role `camera_node/python/camera/dashboard.py` already plays for the
camera station (requirements.md FR-25) — this document is the pick-and-place
equivalent's design pass before the code exists. The gantry itself is a **3-axis
Cartesian stepper gantry** (`Automation_Architecture.md` §5.6/§5.7, `project_charter.md`
§7.4 Decision) — not an articulated arm — which shapes several choices below.

**Status:** design only. `pick_place_node/` has no motion-control or dashboard code yet
(see that folder's `README.md`). Current plan is to build and test the **Digital Twin
Visualization** piece (§3 below) standalone first — as a self-contained view fed by
simulated/mock position data — before wiring it into the rest of the node or writing any
real axis motion-control logic.

## 1. Architecture

Same shape as the camera node's dashboard, which is the working reference pattern in
this repo:

- A `GantryWorker` background thread owns live state (position, vacuum flag, machine
  status) in a thread-safe `SharedState`, matching `camera/worker.py`'s
  `CameraWorker`/`SharedState` split.
- Flask serves `/api/status` (poll) + `/api/command` (POST — jog, home, goto, vacuum
  toggle, e-stop) + the dashboard page itself.
- **Simulated-axis mode by default.** No gantry hardware exists yet (`TODO.md`: "Decide
  pick-and-place axis count, motor/gripper hardware, and gantry travel limits" is still
  open), so `GantryWorker` needs a simulated backend that fabricates plausible motion
  (position ramps toward a jog/goto target at a fixed rate) so the full UI is exercisable
  now. This follows the same dev-machine-first split as the acoustic/camera nodes
  (Development Rule 1 in `.claude/CLAUDE.md`): jog/goto/limit-checking logic stays pure
  and synthetic-input-testable; only the eventual stepper/serial I/O is a thin hardware
  wrapper swapped in later.

## 2. Control Panel (3D-printer-style)

- **Home** — Home All, plus individual Home X / Home Y / Home Z
- **Jog** — X±/Y±/Z± buttons, selectable step size (0.1 / 1 / 10 / 100 mm), optional
  press-and-hold continuous jog
- **Go To Position** — numeric X/Y/Z entry fields + "Move" button; this doubles as the
  live position display (see §2.1) so the same fields show current position and accept
  an override
- **Vacuum** — single Enable/Disable toggle, boolean flag (confirmed, not a variable
  suction level)
- **E-Stop / Pause / Resume**
- **Feedrate/speed override** slider (optional, lower priority)

### 2.1 End-effector position display

Numeric X/Y/Z fields double as both the live readout (updated from `/api/status`) and
the entry point for a manual override ("enter it to modify it and change it as needed" —
2026-08-11 discussion). Editing a field and confirming issues a goto command for that
axis.

## 3. Digital Twin Visualization

The centerpiece: a full graphical, near-real-time representation of the gantry's actual
position and activity, not just numeric readouts.

- **Multiple views, switchable** (tabs/buttons, not simultaneous panes, to keep the
  first version simple): top-down 2D (plan view), front 2D view, and isometric 3D.
- **Rendering approach: hand-built Three.js scene, not URDF.** URDF earns its keep for
  articulated robots with rotational joints and generic forward kinematics; a 3-axis
  Cartesian gantry's pose is fully described by three numbers (X, Y, Z) plus the vacuum
  flag, so a small hand-rolled scene (boxes/cylinders for rails, carriage, Z-column,
  end-effector, positioned directly from live state) is far less dependency weight than
  pulling in a URDF parser/loader stack, and drops into the same Flask dashboard pattern
  the other nodes already use. Revisit only if the end-effector itself becomes a
  multi-joint mechanism.
- End-effector changes color/visual state when vacuum is active.
- Workspace bounds drawn as a wireframe box; carriage highlighted if at a soft limit or
  homing is required.
- Optional later addition: source/destination bin markers once sorting slots are
  defined.

### 3.1 First implementation step (in progress)

Build and test the digital twin view **standalone**, decoupled from `GantryWorker`/
`SharedState`/real command routing — fed by mock or simulated position data so the
visualization itself (view switching, live position-driven rendering, vacuum state
color change) can be verified before any pick-and-place node integration work starts.
Integration into `pick_place_node/` follows only after this is validated.

## 4. Status / Telemetry Panel

- Live position readout (X, Y, Z) — see §2.1, same fields as Go-To
- Machine state: `IDLE / HOMING / JOGGING / MOVING / PICKING / PLACING / ERROR`
- Vacuum status (ON/OFF)
- Current job context: tile ID / grade / source slot / destination slot (once sorting
  logic exists)
- Cycle count (tiles handled this session)
- Last error/alarm + simple event log
- Connection/link status (worker reachable; simulated vs. real hardware)

## 5. Open questions (unresolved as of 2026-08-11)

- Poll-based `/api/status` (like camera's 1 s interval, or faster e.g. ~200 ms) vs.
  WebSocket for smoother twin animation during continuous jogging — polling matches the
  existing pattern and is simpler; WebSocket would look better but adds complexity.
- Whether the simulated-axis worker should enforce real travel limits/speeds now.
  Blocked on the same missing SMTW tile size/weight data called out elsewhere
  (`Automation_Architecture.md`, `requirements.md` Open Items) — likely placeholder
  arbitrary bounds for now, real limits once gantry travel is sized.
