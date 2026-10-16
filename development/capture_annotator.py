"""Screenshot the Tile annotator (Tkinter) on a COPY of a tile dataset, so no real label is changed.

    python development/capture_annotator.py <copy-of-a-tile-dataset-folder>
Windows only; keep the window uncovered while it runs.
"""
import ctypes, sys, time, tkinter as tk
from pathlib import Path

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parent))
import annotate_tiles as at  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "documents" / "deliverables" / "review_2" / "screenshots"


def grab(root, name):
    root.update_idletasks(); root.update(); time.sleep(0.5); root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(OUT / f"{name}.png")
    print("saved", name)


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    root = tk.Tk()
    root.geometry("1280x820+20+20")
    app = at.Annotator(root, Path(sys.argv[1]))
    root.update(); root.lift(); root.attributes("-topmost", True); root.update()
    app.go(6)                                   # a tile well into the set
    grab(root, "annotator_1_tile")
    app.set_defect("chipped"); app.go(-1) if False else None
    app.toggle_context(); grab(root, "annotator_2_context_view")
    app.toggle_context(); app.toggle_readings(); grab(root, "annotator_3_pipeline_readings")
    app.toggle_readings(); app.cycle_zoom(); grab(root, "annotator_4_zoomed")
    root.destroy()


if __name__ == "__main__":
    main()
