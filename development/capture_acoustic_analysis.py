"""Screenshot EVERY screen of the Acoustic-Analysis desktop app (synthetic demo data, no mic).

Builds on Acoustic-Analysis/tools/make_screenshots.py (its demo clip + window grab) and visits all
screens, including the ADVANCED group. Windows only; keep the window uncovered while it runs.

    Acoustic-Analysis\venv\Scripts\python development\capture_acoustic_analysis.py
"""
import re, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AA = ROOT / "Acoustic-Analysis"
sys.path.insert(0, str(AA)); sys.path.insert(0, str(AA / "tools"))
import make_screenshots as ms  # noqa: E402

ms.OUT = ROOT / "documents" / "deliverables" / "review_2" / "screenshots"


def main():
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    from acoustic_analysis.app.main_window import MainWindow
    from acoustic_analysis.app.state import ClipData
    from acoustic_analysis.io.wavstore import save_clip

    tmp = Path(tempfile.mkdtemp(prefix="aa_all_"))
    cfg = ms.load_config()
    cfg["paths"] = {"data_dir": str(tmp), "recordings_dir": str(tmp / "rec"), "database": str(tmp / "d.sqlite"),
                    "exports_dir": str(tmp / "exp"), "reference_profile": str(tmp / "ref.json")}
    win = MainWindow(cfg)
    win.geometry("1320x800+20+20")
    win.update()
    # three analysed clips: good, good, cracked (lower, faster-dying ring)
    for name, tile, hz, tau, seed in (("good-1", 0.3, 2200, 0.05, 60), ("good-2", 0.28, 2260, 0.05, 61),
                                      ("cracked-1", 0.3, 1500, 0.008, 62)):
        x = ms.demo_clip(cfg, tile=tile, seed=seed, ring_hz=hz, tau=tau)
        idx = win.ctx.state.add_clip(ClipData(name=name, samples=x, fs=ms.FS, source="recorded"))
        win.ctx.service.submit(idx)
        end = time.time() + 10
        while time.time() < end and win.ctx.state.clip(idx).features is None:
            win.update(); win.ctx.service.poll(); time.sleep(0.05)
    for s in win.screens:
        try:
            win._goto(s.title)
            ms.grab(win, "acoustic_analysis_screen_" + re.sub(r"[^a-z0-9]+", "-", s.title.lower()).strip("-"))
        except Exception as e:  # noqa: BLE001
            print("skipped", s.title, e)
    win._on_close()


if __name__ == "__main__":
    main()
