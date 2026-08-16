"""Native-window digital twin prototype: same mock gantry motion as
twin_prototype.py, but rendered in a local matplotlib 3D window instead of a
browser (documents/project/Pick_Place_Sorting_Subsystem.md §3.1). No Flask,
no network — just run and a window pops up.

Reuses MockGantrySimulator (pick_place.mock_state) so the motion logic isn't
duplicated between the browser twin and this one; only the rendering differs.

Usage (from pick_place_node/python/, with the venv active):

    python -m pick_place.desktop_twin

Close the window (or Ctrl+C in the terminal) to stop.
"""

from __future__ import annotations

import time

import matplotlib.pyplot as plt
import numpy as np

from pick_place.mock_state import GantryPose, MockGantrySimulator, build_default_patrol, load_config

VACUUM_OFF_COLOR = "#999999"
VACUUM_ON_COLOR = "#5cd65c"
STATE_COLORS = {
    "IDLE": "#aaaaaa",
    "HOMING": "#f0c93d",
    "MOVING": "#5cc8ff",
    "PICKING": "#5cd65c",
    "PLACING": "#5cd65c",
}


def _box_edges(x0, x1, y0, y1, z0, z1):
    """Corner points of a wireframe box, as (xs, ys, zs) line segments for plot()."""
    corners = np.array([
        [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
        [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
    ])
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),  # bottom
        (4, 5), (5, 6), (6, 7), (7, 4),  # top
        (0, 4), (1, 5), (2, 6), (3, 7),  # verticals
    ]
    return corners, edges


class DesktopTwin:
    def __init__(self, simulator: MockGantrySimulator, workspace: dict, tick_hz: float):
        self._sim = simulator
        self._ws = workspace
        self._dt = 1.0 / tick_hz

        self.fig = plt.figure(figsize=(8, 7))
        self.fig.canvas.manager.set_window_title("Pick & Place Node — Digital Twin Prototype (mock data)")
        self.ax = self.fig.add_subplot(111, projection="3d")
        self._setup_scene()

        self._last_tick = time.monotonic()
        self.timer = self.fig.canvas.new_timer(interval=int(self._dt * 1000))
        self.timer.add_callback(self._on_tick)
        self.timer.start()

    def _setup_scene(self):
        ws = self._ws
        x0, x1 = ws["x_min_mm"], ws["x_max_mm"]
        y0, y1 = ws["y_min_mm"], ws["y_max_mm"]
        z0, z1 = ws["z_min_mm"], ws["z_max_mm"]

        ax = self.ax
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        ax.set_zlim(z0, z1)
        ax.set_xlabel("X (mm)")
        ax.set_ylabel("Y (mm)")
        ax.set_zlabel("Z (mm)")
        ax.set_box_aspect((x1 - x0, y1 - y0, max(z1 - z0, 1)))

        corners, edges = _box_edges(x0, x1, y0, y1, z0, z1)
        for i, j in edges:
            ax.plot(*zip(corners[i], corners[j]), color="#444444", linewidth=0.8)

        # Static rails the beam slides along (Y axis), at travel height (z_max).
        ax.plot([x0, x0], [y0, y1], [z1, z1], color="#666666", linewidth=3)
        ax.plot([x1, x1], [y0, y1], [z1, z1], color="#666666", linewidth=3)

        # Dynamic artists, updated every tick.
        (self._beam_line,) = ax.plot([x0, x1], [0, 0], [z1, z1], color="#888888", linewidth=4)
        (self._column_line,) = ax.plot([0, 0], [0, 0], [z1, z0], color="#aaaaaa", linewidth=3)
        self._end_effector = ax.scatter([0], [0], [0], color=VACUUM_OFF_COLOR, s=120, depthshade=False)

        self._title = ax.set_title("", fontsize=10, family="monospace")

    def _on_tick(self):
        now = time.monotonic()
        dt = now - self._last_tick
        self._last_tick = now
        pose = self._sim.advance(dt)
        self._render(pose)
        self.fig.canvas.draw_idle()

    def _render(self, pose: GantryPose):
        z_max = self._ws["z_max_mm"]

        self._beam_line.set_data([self._ws["x_min_mm"], self._ws["x_max_mm"]], [pose.y, pose.y])
        self._beam_line.set_3d_properties([z_max, z_max])

        self._column_line.set_data([pose.x, pose.x], [pose.y, pose.y])
        self._column_line.set_3d_properties([z_max, pose.z])

        self._end_effector._offsets3d = ([pose.x], [pose.y], [pose.z])
        self._end_effector.set_color(VACUUM_ON_COLOR if pose.vacuum_on else VACUUM_OFF_COLOR)

        vac = "ON" if pose.vacuum_on else "OFF"
        self._title.set_text(
            f"X={pose.x:6.1f}  Y={pose.y:6.1f}  Z={pose.z:6.1f}  "
            f"vacuum={vac:<3}  state={pose.machine_state}"
        )
        self._title.set_color(STATE_COLORS.get(pose.machine_state, "#333333"))

    def show(self):
        plt.show()


def main() -> None:
    config = load_config()
    waypoints = build_default_patrol(config)
    simulator = MockGantrySimulator(waypoints, travel_speed_mm_s=config["mock"]["travel_speed_mm_s"])

    twin = DesktopTwin(simulator, workspace=config["workspace"], tick_hz=config["dashboard"]["tick_hz"])
    print("Digital twin window opened — mock/simulated position data, no real gantry hardware. Close the window to stop.")
    twin.show()


if __name__ == "__main__":
    main()
