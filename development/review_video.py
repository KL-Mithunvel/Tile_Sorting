"""Scrub a recording with the pipeline's picks drawn on it, and mark what it got wrong.

    python development\\review_video.py                                   # asks which video
    python development\\review_video.py data\\pi\\recordings\\2026-10-12_10-00-00
    python development\\review_video.py "data\\videos\\WIN_20261011_12_16_03_Pro.mp4"

It runs the shipped pipeline over the video once (cached next to it) and shows every tile
it graded as a green tick on the timeline and a box on that frame. You mark:

    M  MISSED tile     press M, then click the tile that crossed without being graded
    F  FALSE detection the graded "tile" on/near this frame is not a tile
    N  NOTE            any frame worth coming back to (blurry, double tile, odd light)

    <- / ->  1 frame    Shift+<- / ->  10 frames    Space  play / pause
    , / .    previous / next graded tile            [ / ]  previous / next mark
    U        undo last mark    Delete  remove the mark nearest this frame
    E        export every marked frame as a PNG (next to the video)

Saved at once to <video>.review.json (inside the recording folder as review_marks.json),
with the pipeline's counted tiles and a corrected count: counted - false + missed.
This tool is about DETECTION and COUNTING. To say whether a detected tile is chipped or
cracked, use annotate_tiles.py (or the dashboard's live labelling).
"""

from __future__ import annotations

import argparse
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import cv2

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "camera_node" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from video_review_tools import (  # noqa: E402
    GradedTile, ReviewStore, load_cache, nearest_graded, save_cache,
)

VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}
COLORS = {"missed": "#ff4d4d", "false": "#ffa13d", "note": "#4da3ff", "tile": "#3ddc84"}


def resolve_video(path: Path) -> Path:
    """A video file, or a recording folder holding video.mp4/.avi."""
    path = Path(path)
    if path.is_dir():
        for name in ("video.mp4", "video.avi"):
            if (path / name).is_file():
                return path / name
        raise ValueError(f"{path} has no video.mp4 / video.avi in it")
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        raise ValueError(f"{path.name} is not a video file")
    return path


def run_pipeline(video: Path, on_tile, config=None) -> int:
    """Grade every tile of `video` with the shipped pipeline; calls on_tile(GradedTile)."""
    from camera.capture import load_config
    from camera.process_video import process_video_file

    cfg = config or load_config()
    cfg["grading_model"]["enabled"] = False  # the picks and the boxes do not need it

    def cb(record, region, _frame, frame_index):
        on_tile(GradedTile(
            seq=record.seq, frame=int(frame_index), bbox=tuple(int(v) for v in region.bbox),
            grade=record.grade, crack_severity=record.crack.severity,
            corner_broken=bool(record.corner.corner_broken), track_id=record.track_id,
        ))

    return len(process_video_file(str(video), cfg, save_snapshots=False, info={}, on_tile=cb))


class Reviewer:
    def __init__(self, root: tk.Tk, path: Path | None):
        self.root = root
        root.title("Video review")
        root.geometry("1180x800")
        self.cap: cv2.VideoCapture | None = None
        self.video: Path | None = None
        self.store: ReviewStore | None = None
        self.frames = self.w = self.h = 0
        self.fps = 30.0
        self.pos = 0
        self._cap_pos = -2
        self.playing = False
        self.armed: str | None = None  # "missed" while waiting for a click
        self._photo = None
        self._scale = 1.0
        self._offset = (0, 0)
        self.q: queue.Queue = queue.Queue()
        self._pipeline_thread: threading.Thread | None = None
        self._build()
        root.bind("<Key>", self.on_key)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._after_id = root.after(100, self._poll)
        if path is not None:
            root.after(50, lambda: self.open(path))
        else:
            root.after(100, self.ask)

    # ------------------------------------------------------------------ layout
    def _build(self) -> None:
        top = ttk.Frame(self.root, padding=(8, 6))
        top.pack(fill="x")
        ttk.Button(top, text="Open video…", command=self.ask).pack(side="left")
        self.title_var = tk.StringVar(value="No video open")
        ttk.Label(top, textvariable=self.title_var).pack(side="left", padx=10)
        self.status_var = tk.StringVar()
        ttk.Label(top, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).pack(side="right")

        self.canvas = tk.Canvas(self.root, bg="#15171a", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _e: self.render())
        self.canvas.bind("<Button-1>", self.on_click)

        self.tl = tk.Canvas(self.root, height=46, bg="#202225", highlightthickness=0, cursor="hand2")
        self.tl.pack(fill="x", padx=8)
        self.tl.bind("<Button-1>", self._seek_click)
        self.tl.bind("<B1-Motion>", self._seek_click)
        self.tl.bind("<Configure>", lambda _e: self.draw_timeline())

        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(fill="x")
        for text, cmd in (
            ("◀ Prev tile (,)", lambda: self.jump_tile(-1)), ("Next tile (.) ▶", lambda: self.jump_tile(1)),
            ("▶/❚❚ Play (Space)", self.toggle_play),
            ("Missed tile (M)", self.arm_missed), ("False detection (F)", self.mark_false),
            ("Note (N)", self.mark_note), ("Undo (U)", self.undo), ("Export frames (E)", self.export_frames),
        ):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=2)
        self.info_var = tk.StringVar()
        ttk.Label(self.root, textvariable=self.info_var, padding=(10, 0, 10, 8), foreground="#444").pack(fill="x")

    # ------------------------------------------------------------------ opening
    def ask(self) -> None:
        start = REPO_ROOT / "data"
        picked = filedialog.askopenfilename(
            title="Video to review (or cancel, then open a recording folder by path)",
            initialdir=start if start.is_dir() else REPO_ROOT,
            filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv *.m4v"), ("All files", "*.*")])
        if picked:
            self.open(Path(picked))

    def open(self, path: Path) -> None:
        try:
            video = resolve_video(path)
        except ValueError as exc:
            messagebox.showerror("Cannot open", str(exc))
            return
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            messagebox.showerror("Cannot open", f"OpenCV could not open {video.name}")
            return
        if self.cap is not None:
            self.cap.release()
        self.cap, self.video = cap, video
        self.frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.w, self.h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.pos, self._cap_pos, self.playing = 0, -2, False
        self.store = ReviewStore(video, self.fps, self.frames)
        label = video.parent.name if video.stem == "video" else video.name
        self.title_var.set(f"{label}  ·  {self.w}×{self.h}  ·  {self.frames} frames  ·  {self.fps:.1f} fps")
        cached = load_cache(video)
        if cached is not None:
            self.store.graded = cached
            self.status_var.set(f"{len(cached)} tiles graded (cached)")
        else:
            self.start_pipeline()
        self.refresh()

    def start_pipeline(self) -> None:
        if self._pipeline_thread and self._pipeline_thread.is_alive():
            return
        video, store = self.video, self.store
        self.status_var.set("running the pipeline… 0 tiles")
        found: list[GradedTile] = []

        def work() -> None:
            try:
                run_pipeline(video, lambda g: (found.append(g), self.q.put(("progress", len(found)))))
                self.q.put(("done", video, store, found))
            except Exception as exc:  # noqa: BLE001 - shown to the user, not swallowed
                self.q.put(("error", str(exc)))

        self._pipeline_thread = threading.Thread(target=work, daemon=True)
        self._pipeline_thread.start()

    def _poll(self) -> None:
        """Pipeline results arrive on a queue: Tk must only be touched from its own thread."""
        try:
            while True:
                msg = self.q.get_nowait()
                if msg[0] == "progress":
                    self.status_var.set(f"running the pipeline… {msg[1]} tiles")
                elif msg[0] == "done":
                    _, video, store, found = msg
                    if store is self.store:  # the user may have opened another video meanwhile
                        store.graded = sorted(found, key=lambda g: g.frame)
                        try:
                            save_cache(video, store.graded)
                        except OSError:
                            pass  # read-only folder: the review still works, it just reruns next time
                        if store.marks:
                            store.save()
                        self.status_var.set(f"{len(store.graded)} tiles graded")
                        self.refresh()
                elif msg[0] == "error":
                    self.status_var.set("pipeline failed")
                    messagebox.showerror("Pipeline failed", msg[1])
        except queue.Empty:
            pass
        if self.playing and self.cap is not None:
            if self.pos + 1 >= self.frames:
                self.playing = False
            else:
                self.pos += 1
                self.render()
                self.draw_timeline()
        self._after_id = self.root.after(int(1000 / max(self.fps, 1)) if self.playing else 100, self._poll)

    def close(self) -> None:
        try:
            self.root.after_cancel(self._after_id)
        except (tk.TclError, ValueError):
            pass
        if self.cap is not None:
            self.cap.release()
        self.root.destroy()

    # ------------------------------------------------------------------ drawing
    def read_frame(self, i: int):
        if self.cap is None:
            return None
        if i != self._cap_pos + 1:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, frame = self.cap.read()
        self._cap_pos = i if ok else -2
        return frame if ok else None

    def refresh(self) -> None:
        self.render()
        self.draw_timeline()

    def render(self) -> None:
        frame = self.read_frame(self.pos)
        c = self.canvas
        c.delete("all")
        if frame is None:
            return
        cw, ch = max(c.winfo_width(), 50), max(c.winfo_height(), 50)
        self._scale = min(cw / self.w, ch / self.h)
        sw, sh = int(self.w * self._scale), int(self.h * self._scale)
        self._offset = ((cw - sw) // 2, (ch - sh) // 2)
        shown = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_AREA)
        ok, ppm = cv2.imencode(".ppm", shown)
        self._photo = tk.PhotoImage(data=ppm.tobytes())
        c.create_image(*self._offset, image=self._photo, anchor="nw")
        s, (ox, oy) = self._scale, self._offset
        if self.store:
            for g in self.store.graded:
                if abs(g.frame - self.pos) <= 2:
                    x, y, w, h = g.bbox
                    c.create_rectangle(ox + x * s, oy + y * s, ox + (x + w) * s, oy + (y + h) * s, outline=COLORS["tile"], width=2)
                    c.create_text(ox + x * s + 4, oy + y * s + 4, anchor="nw", fill=COLORS["tile"],
                                  text=f"#{g.seq} {g.grade}", font=("Segoe UI", 10, "bold"))
            for m in self.store.marks:
                if abs(m.frame - self.pos) <= 2 and m.x is not None:
                    px, py = ox + m.x * s, oy + m.y * s
                    col = COLORS[m.type]
                    r = 18
                    if m.type == "missed":
                        c.create_line(px - r, py - r, px + r, py + r, fill=col, width=3)
                        c.create_line(px - r, py + r, px + r, py - r, fill=col, width=3)
                        c.create_text(px + r + 4, py, anchor="w", fill=col, text="missed", font=("Segoe UI", 10, "bold"))
                    else:
                        c.create_oval(px - r, py - r, px + r, py + r, outline=col, width=3)
                        c.create_text(px + r + 4, py, anchor="w", fill=col, text=f"not a tile (#{m.seq})", font=("Segoe UI", 10, "bold"))
            for m in self.store.marks:
                if m.type == "note" and abs(m.frame - self.pos) <= 2:
                    c.create_text(ox + 8, oy + 8, anchor="nw", fill=COLORS["note"], text="NOTE: " + m.note, font=("Segoe UI", 11, "bold"))
        if self.armed:
            c.create_text(cw // 2, 14, fill=COLORS["missed"], font=("Segoe UI", 12, "bold"),
                          text="click the missed tile  (Esc to cancel)")
        self.update_info()

    def update_info(self) -> None:
        if not self.store:
            return
        near = nearest_graded(self.store.graded, self.pos, 15)
        s = self.store.summary()
        near_txt = f"   ·   nearest graded: #{near.seq} {near.grade} (crack {near.crack_severity}, corner {'BROKEN' if near.corner_broken else 'ok'})" if near else ""
        self.info_var.set(
            f"frame {self.pos}/{max(self.frames - 1, 0)}  ({self.pos / self.fps:.2f} s){near_txt}\n"
            f"pipeline counted {s['pipeline_counted']}  ·  missed {s['missed']}  ·  false {s['false_detections']}"
            f"  →  corrected count {s['corrected_count']}   ·   notes {s['notes']}")

    def draw_timeline(self) -> None:
        t = self.tl
        t.delete("all")
        if not self.frames:
            return
        w, h = max(t.winfo_width(), 50), 46
        px = lambda f: 6 + (w - 12) * f / max(self.frames - 1, 1)  # noqa: E731
        t.create_rectangle(6, 18, w - 6, 28, fill="#3a3d42", outline="")
        if self.store:
            for g in self.store.graded:
                t.create_line(px(g.frame), 12, px(g.frame), 34, fill=COLORS["tile"], width=2)
            for m in self.store.marks:
                t.create_line(px(m.frame), 4, px(m.frame), 44, fill=COLORS[m.type], width=3)
        t.create_line(px(self.pos), 0, px(self.pos), h, fill="#ffffff", width=2)

    # ------------------------------------------------------------------ actions
    def seek(self, frame: int) -> None:
        if self.cap is None:
            return
        self.pos = max(0, min(self.frames - 1, int(frame)))
        self.refresh()

    def _seek_click(self, e: tk.Event) -> None:
        w = max(self.tl.winfo_width(), 50)
        self.playing = False
        self.seek(round((e.x - 6) / max(w - 12, 1) * (self.frames - 1)))

    def jump_tile(self, step: int) -> None:
        if not self.store or not self.store.graded:
            return
        frames = sorted({g.frame for g in self.store.graded})
        target = next((f for f in frames if f > self.pos), None) if step > 0 else next((f for f in reversed(frames) if f < self.pos), None)
        if target is not None:
            self.seek(target)

    def jump_mark(self, step: int) -> None:
        if not self.store or not self.store.marks:
            return
        frames = sorted({m.frame for m in self.store.marks})
        target = next((f for f in frames if f > self.pos), None) if step > 0 else next((f for f in reversed(frames) if f < self.pos), None)
        if target is not None:
            self.seek(target)

    def toggle_play(self) -> None:
        self.playing = not self.playing

    def arm_missed(self) -> None:
        if self.store is None:
            return
        self.playing = False
        self.armed = "missed"
        self.render()

    def on_click(self, e: tk.Event) -> None:
        self.canvas.focus_set()
        if self.armed != "missed" or self.store is None:
            return
        vx = int((e.x - self._offset[0]) / self._scale)
        vy = int((e.y - self._offset[1]) / self._scale)
        self.armed = None
        if not (0 <= vx < self.w and 0 <= vy < self.h):
            self.render()
            return
        self.store.add_missed(self.pos, vx, vy)
        self.refresh()

    def mark_false(self) -> None:
        if self.store is None:
            return
        try:
            self.store.add_false(self.pos)
        except ValueError as exc:
            messagebox.showinfo("False detection", str(exc))
            return
        self.refresh()

    def mark_note(self) -> None:
        if self.store is None:
            return
        self.playing = False
        text = simpledialog.askstring("Note", f"Note for frame {self.pos}:", parent=self.root)
        if text and text.strip():
            self.store.add_note(self.pos, text)
            self.refresh()

    def undo(self) -> None:
        if self.store and self.store.undo():
            self.refresh()

    def remove_nearest(self) -> None:
        if self.store and self.store.remove_near(self.pos):
            self.refresh()

    def export_frames(self) -> None:
        if not self.store or not self.store.marks:
            messagebox.showinfo("Export", "Nothing is marked yet.")
            return
        out = self.video.with_name(self.video.parent.name + "_marks" if self.video.stem == "video" else self.video.stem + "_marks")
        out.mkdir(exist_ok=True)
        n = 0
        for m in self.store.marks:
            frame = self.read_frame(m.frame)
            if frame is None:
                continue
            if m.x is not None:
                cv2.circle(frame, (m.x, m.y), 40, (0, 0, 255) if m.type == "missed" else (0, 160, 255), 4)
            cv2.imwrite(str(out / f"f{m.frame:06d}_{m.type}.png"), frame)
            n += 1
        self._cap_pos = -2
        self.refresh()
        messagebox.showinfo("Export", f"Saved {n} frame(s) to\n{out}")

    # ------------------------------------------------------------------ keys
    def on_key(self, e: tk.Event) -> None:
        k, ch = e.keysym.lower(), e.char.lower() if e.char else ""
        shift = bool(e.state & 0x1)
        if k == "escape":
            self.armed = None
            self.render()
        elif k == "left":
            self.playing = False
            self.seek(self.pos - (10 if shift else 1))
        elif k == "right":
            self.playing = False
            self.seek(self.pos + (10 if shift else 1))
        elif k == "space":
            self.toggle_play()
        elif ch == ",":
            self.jump_tile(-1)
        elif ch == ".":
            self.jump_tile(1)
        elif ch == "[":
            self.jump_mark(-1)
        elif ch == "]":
            self.jump_mark(1)
        elif ch == "m":
            self.arm_missed()
        elif ch == "f":
            self.mark_false()
        elif ch == "n":
            self.mark_note()
        elif ch == "u":
            self.undo()
        elif k == "delete":
            self.remove_nearest()
        elif ch == "e":
            self.export_frames()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?", type=Path, help="a video file or a recording folder (default: ask)")
    args = ap.parse_args()
    root = tk.Tk()
    Reviewer(root, args.video)
    root.mainloop()


if __name__ == "__main__":
    main()
