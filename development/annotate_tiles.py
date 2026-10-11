"""Small Tkinter tool: look at tile pictures and record YOUR verdict on each.

    python development\\annotate_tiles.py                      # asks which folder
    python development\\annotate_tiles.py data\\tile_dataset\\2026-10-11_17-01-07
    python development\\annotate_tiles.py data\\pi\\camera_captures

Opens either a tile dataset (from export_tile_dataset.py) or any plain folder of
pictures. Two independent verdicts per tile:

    DEFECT  (keys 1-5)   1 fine   2 chipped   3 cracked   4 both   5 unsure
    TIER    (keys 6-9)   6 = 3A   7 = 3B      8 = 4       9 = 5     0 = clear tier
                         (the cosmetic grade; turn off with "Also grade tier")

    <- / A  previous     -> / D  next     N  next unlabelled     Backspace  clear defect
    Z or mouse wheel  zoom (fit / 100% / 200% / 400%), drag to pan     C  tile / belt context
    H  show or hide what the pipeline thought (hidden by default, so it cannot bias you)
    RIGHT-CLICK  mark WHERE the defect is (a point; a crack = a click at each end, a chip =
                 one at the damaged corner). U undoes the last mark. Marks are drawn on
                 the tile and saved with the verdict. (Tile picture only, not context.)
    Enter in the note box  returns to the keys

Every change is saved at once to <folder>/human_labels.json, so closing the window loses
nothing. That file is in the review-sheet export format, so for a dataset folder:

    python development\\tune_from_labels.py <folder>\\human_labels.json
    python development\\export_tile_dataset.py --merge-labels <folder>\\human_labels.json --into <folder>
"""

from __future__ import annotations

import argparse
import base64
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from annotation_tools import DEFECT_LABELS, TIER_LABELS, LabelStore, Tile, load_tiles  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
ZOOMS = (None, 1.0, 2.0, 4.0)  # None = fit the window
DEFECT_KEYS = {str(i + 1): name for i, name in enumerate(DEFECT_LABELS)}
TIER_KEYS = {str(6 + i): name for i, name in enumerate(TIER_LABELS)}
DEFECT_TEXT = {"fine": "Fine", "chipped": "Chipped corner/edge", "cracked": "Cracked", "both": "Chipped + cracked", "unsure": "Unsure"}


def read_image(path: Path):
    """BGR image from a path that may hold non-ASCII characters (cv2.imread cannot)."""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None


def to_photo(bgr) -> tk.PhotoImage:
    ok, png = cv2.imencode(".png", bgr)
    return tk.PhotoImage(data=base64.b64encode(png.tobytes()))


class Annotator:
    def __init__(self, root: tk.Tk, folder: Path | None):
        self.root = root
        root.title("Tile annotator")
        root.geometry("1180x760")
        root.minsize(900, 560)
        self.tiles: list[Tile] = []
        self.store: LabelStore | None = None
        self.kind = ""
        self.index = 0
        self.zoom_i = 0
        self.show_context = False
        self.show_readings = False
        self.offset = [0, 0]
        self._drag = None
        self._photo = None
        self._geom = None  # (scale, px, py, x0, y0, img_w, img_h) of what is on the canvas
        self._cache: dict[Path, np.ndarray] = {}
        self.tier_on = tk.BooleanVar(value=True)
        self.auto_next = tk.BooleanVar(value=True)
        self._build()
        root.bind("<Key>", self.on_key)
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        if folder is not None:
            self.open_folder(folder)
        else:
            root.after(100, self.ask_folder)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=(8, 6))
        top.pack(fill="x")
        ttk.Button(top, text="Open folder…", command=self.ask_folder).pack(side="left")
        self.title_var = tk.StringVar(value="No folder open")
        ttk.Label(top, textvariable=self.title_var).pack(side="left", padx=10)
        self.progress_var = tk.StringVar()
        ttk.Label(top, textvariable=self.progress_var, font=("Segoe UI", 10, "bold")).pack(side="right")

        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg="#202225", highlightthickness=0, cursor="fleur")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.render())
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-3>", self._mark)
        self.canvas.bind("<Button-1>", lambda _e: self.canvas.focus_set(), add="+")

        side = ttk.Frame(body, padding=10, width=290)
        side.pack(side="right", fill="y")
        side.pack_propagate(False)

        self.name_var = tk.StringVar()
        ttk.Label(side, textvariable=self.name_var, wraplength=270, font=("Segoe UI", 9)).pack(anchor="w")
        self.view_var = tk.StringVar()
        ttk.Label(side, textvariable=self.view_var, foreground="#666").pack(anchor="w", pady=(0, 8))

        ttk.Label(side, text="DEFECT", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.defect_buttons: dict[str, tk.Button] = {}
        for key, name in DEFECT_KEYS.items():
            b = tk.Button(side, text=f"{key}   {DEFECT_TEXT[name]}", anchor="w", padx=8,
                          command=lambda n=name: self.set_defect(n))
            b.pack(fill="x", pady=1)
            self.defect_buttons[name] = b

        ttk.Checkbutton(side, text="Also grade tier", variable=self.tier_on, command=self.refresh).pack(anchor="w", pady=(10, 0))
        self.tier_row = ttk.Frame(side)
        self.tier_row.pack(fill="x")
        self.tier_buttons: dict[str, tk.Button] = {}
        for key, name in TIER_KEYS.items():
            b = tk.Button(self.tier_row, text=f"{key}  {name}", command=lambda n=name: self.set_tier(n))
            b.pack(side="left", expand=True, fill="x", padx=1, pady=2)
            self.tier_buttons[name] = b

        ttk.Label(side, text="NOTE", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(10, 0))
        self.note_var = tk.StringVar()
        self.note = ttk.Entry(side, textvariable=self.note_var)
        self.note.pack(fill="x")
        self.note.bind("<Return>", lambda _e: self._note_done())
        self.note.bind("<FocusOut>", lambda _e: self._save_note())
        self.note.bind("<Escape>", lambda _e: self._note_done())

        nav = ttk.Frame(side)
        nav.pack(fill="x", pady=(10, 0))
        ttk.Button(nav, text="◀ Prev", command=lambda: self.go(-1)).pack(side="left", expand=True, fill="x")
        ttk.Button(nav, text="Next ▶", command=lambda: self.go(1)).pack(side="left", expand=True, fill="x")
        ttk.Button(side, text="Next unlabelled  (N)", command=self.go_unlabelled).pack(fill="x", pady=2)
        ttk.Checkbutton(side, text="Auto-advance when done", variable=self.auto_next).pack(anchor="w")

        view = ttk.Frame(side)
        view.pack(fill="x", pady=(8, 0))
        ttk.Button(view, text="Zoom (Z)", command=self.cycle_zoom).pack(side="left", expand=True, fill="x")
        self.ctx_btn = ttk.Button(view, text="Context (C)", command=self.toggle_context)
        self.ctx_btn.pack(side="left", expand=True, fill="x")
        ttk.Button(side, text="Show / hide pipeline's opinion (H)", command=self.toggle_readings).pack(fill="x", pady=2)
        self.readings_var = tk.StringVar()
        ttk.Label(side, textvariable=self.readings_var, justify="left", wraplength=270, foreground="#444").pack(anchor="w", pady=(4, 0))

        self.summary_var = tk.StringVar()
        ttk.Label(side, textvariable=self.summary_var, justify="left", foreground="#444").pack(side="bottom", anchor="w")

    # ------------------------------------------------------------------ folder
    def ask_folder(self) -> None:
        start = REPO_ROOT / "data"
        picked = filedialog.askdirectory(title="Folder of tile pictures or a tile dataset", initialdir=start if start.is_dir() else REPO_ROOT)
        if picked:
            self.open_folder(Path(picked))

    def open_folder(self, folder: Path) -> None:
        try:
            tiles, kind = load_tiles(folder)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Cannot open folder", str(exc))
            return
        self.tiles, self.kind, self.store = tiles, kind, LabelStore(folder, tiles)
        self._cache.clear()
        self.title_var.set(f"{folder.name}  ·  {'tile dataset' if kind == 'dataset' else 'plain pictures'}")
        if kind == "images":
            messagebox.showinfo(
                "Plain pictures",
                "This folder has no pipeline measurements, so these verdicts are a labelled picture "
                "set only. To tune the crack/corner thresholds, label a tile dataset made by "
                "export_tile_dataset.py instead.")
        first = self.store.next_unlabelled(-1)
        self.index = first if first is not None else 0
        self.zoom_i, self.offset = 0, [0, 0]
        self.refresh()

    # ------------------------------------------------------------------ state
    @property
    def tile(self) -> Tile | None:
        return self.tiles[self.index] if self.tiles else None

    def refresh(self) -> None:
        t = self.tile
        if t is None or self.store is None:
            return
        v = self.store.get(t.id)
        self.name_var.set(t.id)
        for name, b in self.defect_buttons.items():
            on = v.label == name
            b.config(relief="sunken" if on else "raised", bg="#7fd18b" if on else self.root.cget("bg"))
        for name, b in self.tier_buttons.items():
            on = v.tier == name
            b.config(relief="sunken" if on else "raised", bg="#8ab4f8" if on else self.root.cget("bg"),
                     state="normal" if self.tier_on.get() else "disabled")
        if self.note_var.get() != v.note and self.root.focus_get() is not self.note:
            self.note_var.set(v.note)
        c = self.store.counts()
        self.progress_var.set(f"{self.index + 1} / {c['total']}   ·   labelled {c['labelled']}")
        self.summary_var.set("   ".join(f"{n} {c[n]}" for n in DEFECT_LABELS))
        self.ctx_btn.config(text="Tile (C)" if self.show_context else "Context (C)")
        if self.show_readings and t.readings:
            r = t.readings
            lines = [f"pipeline grade: {r.get('grade')}", f"crack: {r.get('crack_severity')} ({r.get('crack_length_px', 0):.0f} px)",
                     f"corner: {'BROKEN' if r.get('corner_broken') else 'ok'}  fill {r.get('fill', 0):.3f}  gap {r.get('extent', 0):.3f}"]
            if r.get("model_grade"):
                lines.append(f"model: {r['model_grade']} ({(r.get('model_confidence') or 0) * 100:.0f}%)")
            self.readings_var.set("\n".join(lines))
        elif self.show_readings:
            self.readings_var.set("(no pipeline readings for plain pictures)")
        else:
            self.readings_var.set("pipeline's opinion hidden")
        self.render()

    def render(self) -> None:
        t = self.tile
        if t is None:
            return
        path = t.context if (self.show_context and t.context) else t.image
        img = self._cache.get(path)
        if img is None:
            img = read_image(path)
            if img is None:
                self.canvas.delete("all")
                self.canvas.create_text(20, 20, anchor="nw", fill="#f88", text=f"Cannot read {path.name}")
                return
            if len(self._cache) > 24:
                self._cache.clear()
            self._cache[path] = img
        cw, ch = max(self.canvas.winfo_width(), 50), max(self.canvas.winfo_height(), 50)
        h, w = img.shape[:2]
        zoom = ZOOMS[self.zoom_i]
        scale = min(cw / w, ch / h) if zoom is None else zoom
        sw, sh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        interp = cv2.INTER_AREA if scale < 1 else (cv2.INTER_LINEAR if scale < 1.5 else cv2.INTER_NEAREST)
        shown = cv2.resize(img, (sw, sh), interpolation=interp)
        # pan: clamp so the picture cannot be dragged off-screen
        ox = int(min(max(self.offset[0], min(0, cw - sw)), max(0, 0)))
        oy = int(min(max(self.offset[1], min(0, ch - sh)), max(0, 0)))
        self.offset = [ox, oy]
        x0, y0 = max(0, -ox), max(0, -oy)
        view = shown[y0:y0 + ch, x0:x0 + cw]
        self._photo = to_photo(view)
        px = (cw - view.shape[1]) // 2 if sw <= cw else 0
        py = (ch - view.shape[0]) // 2 if sh <= ch else 0
        self.canvas.delete("all")
        self.canvas.create_image(px, py, image=self._photo, anchor="nw")
        self._geom = (scale, px, py, x0, y0, w, h)
        if path == t.image and self.store is not None:
            for m in self.store.get(t.id).marks:
                cx, cy = px + m["x"] * scale - x0, py + m["y"] * scale - y0
                r = 9
                self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#ff4d4d", width=2)
                self.canvas.create_line(cx - r - 4, cy, cx + r + 4, cy, fill="#ff4d4d")
                self.canvas.create_line(cx, cy - r - 4, cx, cy + r + 4, fill="#ff4d4d")
        label = "fit" if zoom is None else f"{int(zoom * 100)}%"
        n = len(self.store.get(t.id).marks) if self.store else 0
        self.view_var.set(f"{w}×{h} px  ·  zoom {label}  ·  {'context' if path == t.context else 'tile'}"
                          + (f"  ·  {n} mark{'s' if n != 1 else ''}" if n else ""))

    # ------------------------------------------------------------------ actions
    def go(self, step: int) -> None:
        if not self.tiles:
            return
        self._save_note()
        self.index = (self.index + step) % len(self.tiles)
        self.offset = [0, 0]
        self.refresh()
        self.canvas.focus_set()

    def go_unlabelled(self) -> None:
        if self.store is None:
            return
        self._save_note()
        nxt = self.store.next_unlabelled(self.index)
        if nxt is None:
            messagebox.showinfo("All done", "Every tile has a defect label.")
            return
        self.index, self.offset = nxt, [0, 0]
        self.refresh()
        self.canvas.focus_set()

    def set_defect(self, name: str) -> None:
        if self.store is None or self.tile is None:
            return
        cur = self.store.get(self.tile.id).label
        self.store.set_label(self.tile.id, None if cur == name else name)  # same key again = undo
        self._maybe_advance()

    def set_tier(self, name: str) -> None:
        if self.store is None or self.tile is None or not self.tier_on.get():
            return
        cur = self.store.get(self.tile.id).tier
        self.store.set_tier(self.tile.id, None if cur == name else name)
        self._maybe_advance()

    def _maybe_advance(self) -> None:
        v = self.store.get(self.tile.id)
        done = v.label is not None and (v.tier is not None or not self.tier_on.get())
        if self.auto_next.get() and done:
            self.go(1)
        else:
            self.refresh()

    def _save_note(self) -> None:
        if self.store is None or self.tile is None:
            return
        if self.note_var.get().strip() != self.store.get(self.tile.id).note:
            self.store.set_note(self.tile.id, self.note_var.get())

    def _note_done(self) -> None:
        self._save_note()
        self.canvas.focus_set()
        self.refresh()

    def cycle_zoom(self) -> None:
        self.zoom_i = (self.zoom_i + 1) % len(ZOOMS)
        self.offset = [0, 0]
        self.render()

    def toggle_context(self) -> None:
        if self.tile and self.tile.context:
            self.show_context = not self.show_context
            self.offset = [0, 0]
            self.refresh()

    def toggle_readings(self) -> None:
        self.show_readings = not self.show_readings
        self.refresh()

    # ------------------------------------------------------------------ input
    def on_key(self, e: tk.Event) -> None:
        if self.root.focus_get() is self.note:
            return  # typing a note: keys are text
        k = e.keysym.lower()
        if e.char in DEFECT_KEYS:
            self.set_defect(DEFECT_KEYS[e.char])
        elif e.char in TIER_KEYS:
            self.set_tier(TIER_KEYS[e.char])
        elif e.char == "0" and self.store and self.tile:
            self.store.set_tier(self.tile.id, None)
            self.refresh()
        elif k in ("left", "a"):
            self.go(-1)
        elif k in ("right", "d", "space"):
            self.go(1)
        elif k == "n":
            self.go_unlabelled()
        elif k == "u" and self.store and self.tile:
            if self.store.remove_last_mark(self.tile.id):
                self.render()
        elif k == "backspace" and self.store and self.tile:
            self.store.set_label(self.tile.id, None)
            self.refresh()
        elif k == "z":
            self.cycle_zoom()
        elif k == "c":
            self.toggle_context()
        elif k == "h":
            self.toggle_readings()

    def _wheel(self, e: tk.Event) -> None:
        if e.delta > 0 and self.zoom_i < len(ZOOMS) - 1:
            self.zoom_i += 1
        elif e.delta < 0 and self.zoom_i > 0:
            self.zoom_i -= 1
        else:
            return
        self.offset = [0, 0]
        self.render()

    def _mark(self, e: tk.Event) -> None:
        """Right-click: record a defect point at this spot of the TILE picture."""
        t = self.tile
        if self.store is None or t is None or self._geom is None:
            return
        if self.show_context and t.context:
            self.view_var.set("marks go on the tile picture - press C to switch back")
            return
        scale, px, py, x0, y0, w, h = self._geom
        ix, iy = int((e.x - px + x0) / scale), int((e.y - py + y0) / scale)
        if not (0 <= ix < w and 0 <= iy < h):
            return
        label = self.store.get(t.id).label
        kind = {"chipped": "chip", "cracked": "crack"}.get(label, "defect")
        self.store.add_mark(t.id, ix, iy, kind)
        self.render()

    def _drag_start(self, e: tk.Event) -> None:
        self._drag = (e.x, e.y, self.offset[0], self.offset[1])

    def _drag_move(self, e: tk.Event) -> None:
        if self._drag:
            x, y, ox, oy = self._drag
            self.offset = [ox + e.x - x, oy + e.y - y]
            self.render()


def newest_dataset() -> Path | None:
    root = REPO_ROOT / "data" / "tile_dataset"
    runs = sorted(p for p in root.glob("*") if (p / "labels.csv").is_file()) if root.is_dir() else []
    return runs[-1] if runs else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", nargs="?", type=Path, help="a tile dataset or a folder of pictures (default: ask)")
    args = ap.parse_args()
    root = tk.Tk()
    Annotator(root, args.folder)
    root.mainloop()


if __name__ == "__main__":
    main()
