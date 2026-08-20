"""Dev-only launcher for pick_place_node's digital twin GUI (mock/simulated
gantry motion — no real GantryWorker or hardware exists yet, see
documents/project/Pick_Place_Sorting_Subsystem.md §3.1).

Not part of any App Bricks node — like the rest of development/, this is a
convenience wrapper so the twin can be launched with `python
development\\run_pick_place_twin.py` from the repo root, the same way
tile_param_tuner.py/analyze_dataset.py are, instead of having to `cd
pick_place_node\\python` and run the `pick_place.*` modules directly.

Two GUIs, same underlying MockGantrySimulator (pick_place.mock_state) and
pick_place/config.yaml — only the rendering differs:

  --mode desktop (default)  Native matplotlib 3D window, no browser, no
                             network. Closes when you close the window.
  --mode browser             Flask + vendored Three.js dashboard on
                             http://<this machine's IP>:5050/, reachable from
                             any browser on the same WiFi (Ctrl+C to stop).

Usage:
    venv\\Scripts\\activate
    python development\\run_pick_place_twin.py
    python development\\run_pick_place_twin.py --mode browser
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "pick_place_node" / "python"))


def run_desktop() -> None:
    from pick_place.desktop_twin import main as desktop_main

    desktop_main()


def run_browser() -> None:
    from pick_place.twin_prototype import main as browser_main

    browser_main()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--mode",
        choices=["desktop", "browser"],
        default="desktop",
        help="desktop: native matplotlib window (default). browser: Flask+Three.js dashboard over WiFi.",
    )
    args = parser.parse_args()

    if args.mode == "desktop":
        run_desktop()
    else:
        run_browser()


if __name__ == "__main__":
    main()
