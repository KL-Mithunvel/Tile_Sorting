"""Builds every figure used by ../Final_Report.md.

Run from the repo root:   .venv\\Scripts\\python.exe documents\\deliverables\\review_2\\figures\\make_figures.py

Rules (see Final_Report.md, "Keeping this report current"):
  * Diagrams are drawn here, in code, so they can be regenerated when the design changes.
  * Every number on a chart is READ from a project file (labels, benchmark JSON) or is
    written in the DATA block below with the document it came from. Nothing is invented.
  * Pictures of real tiles are taken from data/ (gitignored); if data/ is missing those
    figures are skipped, the rest still build.
  * Synthetic signals are labelled SYNTHETIC on the figure itself.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Polygon, Rectangle

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = HERE
DATASET = ROOT / "data" / "tile_dataset" / "2026-10-11_17-01-07"
VIDEOS = ROOT / "data" / "videos"

sys.path.insert(0, str(ROOT / "camera_node" / "python"))
sys.path.insert(0, str(ROOT / "acoustic_node" / "python"))

# ---- palette (one set, used everywhere) -----------------------------------
C_CAM = "#2f6fb0"      # camera / Pi
C_AC = "#c0562b"       # acoustic
C_PP = "#3b8a5a"       # pick and place / UNO Q
C_NET = "#6b5b95"      # network / broker
C_NANO = "#b08a1e"     # nano / belt
C_GREY = "#5b6470"
C_BG = "#f6f7f9"
C_OK = "#3b8a5a"
C_WARN = "#d99a1e"
C_BAD = "#b3382c"
GRADE_COL = {"Grade A": C_OK, "Grade B": C_WARN, "Grade C": "#d9701e", "Reject": C_BAD, "Hold": C_GREY}

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.dpi": 100,
    "savefig.dpi": 170,
    "savefig.bbox": "tight",
    "savefig.facecolor": "white",
})

# ---- DATA copied from project documents (source in the comment) ------------
# camera_models/cam_edge/results/accuracy.json + README.md "Results - dev laptop, 2026-09-20"
MODEL_POINTS = [  # name, accuracy %, ms per tile (4 threads), onnx MB
    ("yolo26n-cls", 82.9, 1.59, 6.2),
    ("yolo26s-cls", 85.5, 4.16, 21.8),
    ("ViT-base", 92.1, 110.2, 343.5),
    ("ViT-base INT8", 92.1, 48.7, 87.1),
    ("yolo26n INT8", 57.9, 19.1, 1.6),
    ("yolo26s INT8", 36.8, 64.7, 5.6),
]
# camera_node_handover.md section 4, measured on 378 intact photos with development/corner_sensitivity.py
CORNER_TRADE = [  # min_fill_ratio / max_extent, % of good tiles rejected, smallest chip caught (in)
    ("0.83 / 0.22 (was shipped)", 1.9, 5.0),
    ("0.88 / 0.15", 3.7, 4.0),
    ("0.92 / 0.10", 5.6, 2.7),
]
BELT_PX_PER_S = 435.0   # camera_node_handover.md section 4: 14.5 px/frame x 30 fps


def save(fig, name):
    fig.savefig(OUT / name)
    plt.close(fig)
    print("wrote", name)


# ---- drawing helpers --------------------------------------------------------
def box(ax, x, y, w, h, text, fc="#ffffff", ec=C_GREY, fs=8.5, bold=False, tc="black", r=0.02, lw=1.2):
    p = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}",
                       fc=fc, ec=ec, lw=lw, zorder=2)
    ax.add_patch(p)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            fontweight="bold" if bold else "normal", color=tc, zorder=3, linespacing=1.25)
    return (x, y, w, h)


def arrow(ax, a, b, text=None, color=C_GREY, style="-|>", rad=0.0, fs=7.5, ls="-", ty=0.0, tx=0.0, lw=1.3):
    ar = FancyArrowPatch(a, b, arrowstyle=style, mutation_scale=11, color=color, lw=lw,
                         connectionstyle=f"arc3,rad={rad}", linestyle=ls, zorder=1)
    ax.add_patch(ar)
    if text:
        ax.text((a[0] + b[0]) / 2 + tx, (a[1] + b[1]) / 2 + ty, text, fontsize=fs, ha="center",
                va="center", color=color, zorder=4,
                bbox=dict(fc="white", ec="none", pad=0.6, alpha=0.9))


def diamond(ax, cx, cy, w, h, text, fc="#fff7e0", ec=C_WARN, fs=8):
    ax.add_patch(Polygon([(cx, cy + h / 2), (cx + w / 2, cy), (cx, cy - h / 2), (cx - w / 2, cy)],
                         closed=True, fc=fc, ec=ec, lw=1.2, zorder=2))
    ax.text(cx, cy, text, ha="center", va="center", fontsize=fs, zorder=3, linespacing=1.2)


def canvas(w, h, title=None):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100 * h / w)
    ax.set_aspect("equal")
    ax.axis("off")
    if title:
        ax.set_title(title, fontsize=11, fontweight="bold", loc="left")
    return fig, ax


# =============================================================================
# 1. System architecture
# =============================================================================
def fig_architecture():
    fig, ax = canvas(11, 9.0)
    top = 70
    # network backbone
    ax.add_patch(Rectangle((3, 44), 94, 3.2, fc=C_NET, ec="none", zorder=1))
    ax.text(50, 45.6, "D-Link isolated control LAN  192.168.1.0/24   (MQTT broker = mosquitto on the Raspberry Pi)",
            ha="center", va="center", color="white", fontsize=8.5, fontweight="bold", zorder=3)

    # Pi
    box(ax, 4, 52, 44, 28, "", fc="#eaf1fa", ec=C_CAM, lw=1.8)
    ax.text(26, 77.5, "Raspberry Pi 5 (16 GB)  -  station decision computer", ha="center", fontsize=9, fontweight="bold", color=C_CAM)
    box(ax, 6, 62, 19, 12, "tile-camera\nUSB / CSI camera\nsegment -> crack -> corner\n-> (model) -> grade", fc="white", ec=C_CAM, fs=7.5)
    box(ax, 27, 62, 19, 12, "tile-acoustic\nmicrophone + FFT\nledger + fusion\nfinal grade, SQLite", fc="white", ec=C_AC, fs=7.5)
    box(ax, 6, 54, 40, 5.5, "mosquitto broker  |  operator GUI :5001  |  camera dashboard :5000", fc="white", ec=C_NET, fs=7.5)

    # Nano
    box(ax, 53, 56, 20, 22, "Arduino Nano\n(planned)\n\nstriker release\nbelt PWM + watchdog\nToF 3 (belt end)\ne-stop (upstream)", fc="#fbf4df", ec=C_NANO, fs=7.5)
    # UNO Q
    box(ax, 76, 56, 20, 22, "Arduino UNO Q\n\npick-and-place\ngantry firmware (MCU)\nsequencer + HMI (Linux)\nstepper x4, encoders,\nlimits, vacuum", fc="#e9f5ee", ec=C_PP, fs=7.5)

    # verticals to LAN
    for x in (26, 63, 86):
        arrow(ax, (x, 52 if x == 26 else 56), (x, 47.2), color=C_NET, style="<|-|>")
    ax.text(26, 49.6, "", fontsize=7)

    # Master
    box(ax, 40, 31, 22, 9, "Master PC (optional)\nobserve / collect / command  :8000", fc="#f1ecf8", ec=C_NET, fs=7.5)
    arrow(ax, (51, 40), (51, 44), color=C_NET, style="<|-|>")

    # physical line row
    ax.text(3, 26, "Physical line (left to right)", fontsize=9, fontweight="bold", color=C_GREY)
    names = [("Feed\n(manual)", C_GREY), ("Camera\nstation\nlight + lens", C_CAM), ("Acoustic\nstation\nball-drop + mic", C_AC),
             ("Dimension\n(not built)", "#9aa1ab"), ("Belt end\nToF 1", C_NANO), ("Cartesian\ngantry\nvacuum cup", C_PP), ("Bins A / B / C\nReject / Hold", C_GREY)]
    n = len(names)
    xs = np.linspace(4, 78, n)
    for i, (t, c) in enumerate(names):
        ls = "--" if "not built" in t else "-"
        p = FancyBboxPatch((xs[i], 8), 10.0, 14, boxstyle="round,pad=0,rounding_size=0.02", fc="white", ec=c, lw=1.6, ls=ls)
        ax.add_patch(p)
        ax.text(xs[i] + 5.0, 15, t, ha="center", va="center", fontsize=7.4)
        if i:
            arrow(ax, (xs[i - 1] + 10.0, 15), (xs[i], 15), color=C_GREY)
    ax.add_patch(Rectangle((4, 4.5), 84.5, 1.8, fc="#4b4f56", ec="none"))
    ax.text(46, 5.4, "conveyor belt  (DC motor, open-loop PWM)", color="white", fontsize=7, ha="center", va="center")
    save(fig, "fig_architecture.png")


# =============================================================================
# 2. Methodology flow
# =============================================================================
def fig_method():
    fig, ax = canvas(8.6, 10.6)
    steps = [
        ("1  Problem study", "SMTW: manual inspection, grading,\nsorting, packing - variable, tiring, no records", "#eef2f7"),
        ("2  Requirements + architecture", "charter -> 22 FR + 7 NFR; stations, nodes,\nMQTT contract, fail-closed rules", "#eef2f7"),
        ("3  Pure logic first", "algorithm with no I/O: synthetic input,\nunit test (hardware wrapper kept thin)", "#e6f0fa"),
        ("4  Real data on the laptop", "380 intact-tile photos, 5 belt videos (92 tiles),\nlive webcam; calibrate thresholds", "#e6f0fa"),
        ("5  Measure, label, correct", "human review sheet -> 88 labels;\nfind faults (see Chapter 5), fix, re-test", "#e6f0fa"),
        ("6  Integrate stations", "camera + acoustic + fusion + UNO Q feed over\nMQTT; demo mode with synthetic peers", "#e9f5ee"),
        ("7  Bench hardware (next)", "Pi 5, CSI camera, mic + tap rig, Nano,\nUNO Q gantry; repeat steps 4-5 on hardware", "#fbf4df"),
        ("8  Pilot at SMTW (future)", "real tile sizes, throughput, false-reject\ncost, operator acceptance", "#fbf4df"),
    ]
    y = 100 * 10.6 / 8.6 - 13
    h = 9.2
    prev = None
    for i, (t, d, fc) in enumerate(steps):
        yy = y - i * (h + 4.2)
        box(ax, 10, yy, 80, h, "", fc=fc, ec=C_GREY, r=0.02)
        ax.text(14, yy + h - 2.7, t, fontsize=10, fontweight="bold", va="center")
        ax.text(14, yy + 3.1, d, fontsize=8.3, va="center", color="#333")
        if prev is not None:
            arrow(ax, (50, prev), (50, yy + h), color=C_GREY)
        prev = yy
    # feedback loop
    ax.add_patch(FancyArrowPatch((90, y - 4 * (h + 4.2) + h / 2), (90, y - 2 * (h + 4.2) + h / 2),
                                 connectionstyle="arc3,rad=-0.0", arrowstyle="-|>", mutation_scale=11, color=C_BAD, lw=1.3, linestyle="--"))
    ax.text(93.5, y - 3 * (h + 4.2) + h / 2, "wrong?\nfix the\nlogic, add\na test", fontsize=7.5, color=C_BAD, va="center", ha="center")
    # done / not done legend
    ax.text(10, 8, "blue = done on laptop   green = built, laptop-tested only   amber = not yet done", fontsize=7.8, color=C_GREY)
    save(fig, "fig_method.png")


# =============================================================================
# 3. Camera pipeline
# =============================================================================
def fig_camera_pipeline():
    fig, ax = canvas(11, 6.6)
    b = lambda x, y, w, h, t, fc="white", ec=C_CAM, fs=7.8: box(ax, x, y, w, h, t, fc=fc, ec=ec, fs=fs)
    b(2, 42, 13, 14, "Camera\nUSB / CSI\n1080p @ 30 fps\nlatest-frame\nreader")
    b(18, 42, 14, 14, "Belt ROI\nfind belt by\nits colour;\nfail-closed\nif not seen")
    b(35, 42, 14, 14, "Segment\nHSV tile colour\ninside ROI +\nshape gate\n(fill, aspect)")
    b(52, 42, 14, 14, "Track\nnearest-centre\nid per tile;\n1 detector\nper tile")
    b(69, 42, 14, 14, "Trigger line\nauto: perpendicular\nto belt;\nfires once\nper tile")
    b(86, 42, 12, 14, "Pick\nsharpest\nframe\n(+/-6 %)", fc="#eaf1fa")
    for x0, x1 in [(15, 18), (32, 35), (49, 52), (66, 69), (83, 86)]:
        arrow(ax, (x0, 49), (x1, 49), color=C_CAM)
    # second row
    b(10, 14, 16, 16, "Crack check\nCanny edges,\nborder band blanked,\nlong + thin contour\n-> none/minor/major", ec=C_AC)
    b(30, 14, 16, 16, "Corner check\nfill ratio, gap depth,\naspect ratio,\nnotch depth\n(+ inches)", ec=C_AC)
    b(50, 14, 16, 16, "Model (optional)\nONNX tier 3A/3B/4/5\nvotes only if\nconfidence >= 0.6", ec=C_WARN)
    b(70, 14, 14, 16, "Fuse\nworse of rules\nand model", fc="#fff7e0", ec=C_WARN)
    b(87, 14, 11.5, 16, "Grade A / B\n/ C / Reject\n+ snapshot\n+ MQTT", fc="#e9f5ee", ec=C_OK)
    arrow(ax, (92, 42), (92, 31), color=C_CAM, text="one tile\nimage", rad=0, tx=-4.5)
    for x0, x1 in [(26, 30), (46, 50), (66, 70), (84, 87)]:
        arrow(ax, (x0, 22), (x1, 22), color=C_GREY)
    ax.text(2, 4, "Live view: every frame is segmented and streamed (MJPEG);  the three checks above run ONCE per tile.\n"
                  "Same code runs on the laptop webcam, on recorded video (process_video.py) and, planned, on the Pi 5 + CSI camera.",
            fontsize=8, color=C_GREY, va="bottom")
    save(fig, "fig_camera_pipeline.png")


# =============================================================================
# 4. Per-tile decision flow (fail-closed)
# =============================================================================
def fig_decision_flow():
    fig, ax = canvas(11, 10.0)
    box(ax, 38, 80, 24, 6, "Camera grades tile at line\npublish tile/<id>/camera/result", ec=C_CAM, fs=7.8)
    box(ax, 38, 68, 24, 6, "Ledger: tile = awaiting_tap\n(first in, first out)", ec=C_NET, fs=7.8)
    arrow(ax, (50, 80), (50, 74), color=C_GREY)
    diamond(ax, 50, 58, 22, 11, "strike heard\nwithin max_wait_s?")
    arrow(ax, (50, 68), (50, 63.5), color=C_GREY)
    box(ax, 4, 52, 24, 12, "NO  ->  tile = HOLD\n(manual bin)\ncamera Reject stays Reject\nalarm: timeout", ec=C_BAD, fs=7.5, fc="#fbeceb")
    arrow(ax, (39, 58), (28, 58), color=C_BAD, text="no", ty=1.8)
    box(ax, 38, 38, 24, 7, "Measure tap: peak Hz, centroid,\nSNR, decay T20  (acoustic/features.py)", ec=C_AC, fs=7.5)
    arrow(ax, (50, 52.5), (50, 45), color=C_GREY, text="yes: bind to OLDEST waiting tile", ty=0, tx=0, fs=7.2)
    diamond(ax, 50, 28, 26, 11, "sound counted?\nvote_enabled, valid tap,\ngrade exists, conf >= min")
    arrow(ax, (50, 38), (50, 33.5), color=C_GREY)
    box(ax, 70, 20, 26, 9, "NO -> final = camera grade\n(sound shown as 'not counted')\nthis is today's default", ec=C_GREY, fs=7.5)
    arrow(ax, (63, 28), (70, 26), color=C_GREY, text="no", ty=1.8)
    box(ax, 38, 8, 24, 8, "YES -> final = WORSE of\ncamera and sound grade", ec=C_OK, fs=7.8, fc="#e9f5ee")
    arrow(ax, (50, 22.5), (50, 16), color=C_OK, text="yes", tx=2.5)
    box(ax, 4, 20, 26, 11, "Also fail-closed (tested):\norphan tap, empty strike,\nmissed camera -> UNKNOWN Hold,\nduplicate message, seq gap", ec=C_BAD, fs=7.2, fc="#fbeceb")
    box(ax, 72, 5, 25, 9, "final -> UNO Q  tile/<id>/final\nUNO Q acks;  no ack -> resend,\nthen link DOWN, queue in order", ec=C_PP, fs=7.4, fc="#e9f5ee")
    arrow(ax, (62, 12), (72, 9.5), color=C_PP)
    arrow(ax, (84.5, 20), (84.5, 14), color=C_GREY, ls="--")
    save(fig, "fig_decision_flow.png")


# =============================================================================
# 5. Tap sequencer FSM
# =============================================================================
def fig_tap_fsm():
    fig, ax = canvas(10.5, 5.6)
    states = [
        ("WAIT_TILE", "ToF sees no tile", 3, 34),
        ("SETTLE", "tile stops;\nvibration dies", 22, 34),
        ("ARM", "ARM solenoid\nholds one ball", 41, 34),
        ("ARM_SETTLE", "ball seated", 60, 34),
        ("RELEASE", "LOCK opens:\nball falls\n= IMPACT", 79, 34),
        ("COOLDOWN", "ring-down,\ncapture window", 79, 8),
    ]
    for name, d, x, y in states:
        fc = "#fdece4" if name == "RELEASE" else "white"
        box(ax, x, y, 14, 14, "", fc=fc, ec=C_AC, lw=1.6)
        ax.text(x + 7, y + 10, name, ha="center", fontsize=8.4, fontweight="bold", color=C_AC)
        ax.text(x + 7, y + 4.6, d, ha="center", va="center", fontsize=7.3)
    for x0, x1, lab in [(17, 22, "tile present"), (36, 41, "settle_s"), (55, 60, "arm_s"), (74, 79, "arm_settle_s")]:
        arrow(ax, (x0, 41), (x1, 41), color=C_AC)
        ax.text((x0 + x1) / 2, 50.8, lab, ha="center", fontsize=7.2, color=C_AC)
    arrow(ax, (86, 34), (86, 22), color=C_AC)
    ax.text(88, 28, "tap_fired = True\n(one call only)", fontsize=7.2, color=C_AC, va="center")
    ax.plot([79, 10], [15, 15], color=C_GREY, lw=1.3)
    ax.add_patch(FancyArrowPatch((10, 15), (10, 34), arrowstyle="-|>", mutation_scale=11, color=C_GREY, lw=1.3))
    ax.text(45, 17, "cooldown_s elapsed, then the tile must leave (edge) before a new cycle starts",
            ha="center", fontsize=7.4, color=C_GREY)
    ax.text(2, 3, "Mirrors acoustic_node/sketch/sketch.ino. Pure Python version: acoustic/tap_sequencer.py (unit-tested).\n"
                  "Neither ToF sensor, solenoid nor MOSFET driver has been wired yet - the FSM has run in simulation only.",
            fontsize=7.8, color=C_GREY)
    save(fig, "fig_tap_fsm.png")


# =============================================================================
# 6. MQTT sequence
# =============================================================================
def fig_sequence():
    fig, ax = plt.subplots(figsize=(10.5, 6.2))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 62)
    ax.axis("off")
    actors = [("Camera\nservice", 12, C_CAM), ("Broker\n(Pi)", 36, C_NET), ("Station\n(fusion)", 60, C_AC), ("UNO Q\nfeed", 86, C_PP)]
    for n, x, c in actors:
        ax.text(x, 59, n, ha="center", va="center", fontsize=9, fontweight="bold", color=c)
        ax.plot([x, x], [4, 55], color=c, lw=1, ls=":")
    msgs = [
        (12, 36, 50, "tile/T..-1/camera/result  (QoS 1)", C_CAM),
        (36, 60, 50, "", C_NET),
        (60, 60, 44, "ledger: awaiting_tap", C_AC),
        (60, 60, 39, "strike heard -> measure -> bind to oldest tile", C_AC),
        (60, 36, 33, "tile/T..-1/acoustic/result", C_AC),
        (60, 36, 27, "tile/T..-1/final  (worse-of rule)", C_AC),
        (36, 86, 24, "", C_NET),
        (86, 36, 18, "tile/T..-1/ack", C_PP),
        (36, 60, 15, "", C_NET),
        (60, 36, 9, "no ack in ack_timeout_s -> resend final (attempt + 1)", C_BAD),
    ]
    for a, b, y, t, c in msgs:
        if a == b:
            ax.text(a + 2, y, t, fontsize=7.8, va="center", color=c)
        else:
            ax.annotate("", xy=(b, y), xytext=(a, y), arrowprops=dict(arrowstyle="-|>", color=c, lw=1.3))
            if t:
                ax.text((a + b) / 2, y + 1.7, t, fontsize=7.6, ha="center", color=c)
    ax.text(50, 3.5, "UNO Q de-duplicates on tile_id and acts once; Hold = manual bin, never 'skip'.", ha="center", fontsize=8, color=C_GREY)
    ax.set_title("One tile's messages (contract in documents/programming/station_coordination.md section 4)", fontsize=10, loc="left", fontweight="bold")
    save(fig, "fig_sequence.png")


# =============================================================================
# 7. Grade fusion
# =============================================================================
def fig_grade_fusion():
    fig, ax = canvas(10.5, 6.2)
    box(ax, 3, 42, 36, 14, "RULES (measured defects)\ncorner broken or major crack -> Reject\nminor crack -> Grade B;  otherwise Grade A", ec=C_AC, fs=7.8)
    box(ax, 3, 24, 36, 14, "SOUND (optional)\nvote_enabled = False until validated on real\ntiles (station.reference = null)", ec=C_NET, fs=7.8)
    box(ax, 3, 6, 36, 14, "MODEL (cosmetic tier)\n3A -> A    3B -> B    4 -> C    5 -> Reject\nvotes only if confidence >= 0.6", ec=C_WARN, fs=7.8)
    diamond(ax, 60, 31, 22, 18, "WORSE OF\nthe votes\nthat count", ec=C_BAD, fc="#fbeceb")
    arrow(ax, (39, 49), (52, 34), color=C_GREY)
    arrow(ax, (39, 31), (49, 31), color=C_GREY)
    arrow(ax, (39, 13), (52, 28), color=C_GREY)
    box(ax, 77, 22, 21, 18, "Final grade\nA / B / C / Reject / Hold\n\nA vote can only LOWER\na grade, never rescue one", ec=C_OK, fs=7.5, fc="#e9f5ee")
    arrow(ax, (71, 31), (77, 31), color=C_OK)
    ax.text(3, 1.5, "Order used for 'worse': Grade A < Grade B < Grade C < Reject   (camera/pipeline.py GRADE_ORDER)", fontsize=7.8, color=C_GREY)
    save(fig, "fig_grade_fusion.png")


# =============================================================================
# 8. Synthetic tap (clearly labelled)
# =============================================================================
def fig_acoustic_synthetic():
    try:
        from acoustic.signal_processing import compute_fft, compute_rms, dominant_frequency
    except Exception as exc:  # pragma: no cover
        print("skip acoustic figure:", exc)
        return
    sr = 44100
    t = np.arange(int(0.25 * sr)) / sr
    rng = np.random.default_rng(7)

    def tap(f0, tau, extra=None):
        s = np.exp(-t / tau) * np.sin(2 * np.pi * f0 * t) * 0.8
        s += 0.3 * np.exp(-t / (tau * 0.6)) * np.sin(2 * np.pi * f0 * 2.4 * t)
        if extra is not None:
            s += extra
        s += rng.normal(0, 0.004, t.size)
        return s

    good = tap(2850, 0.045)
    # a cracked tile in a physical model: lower frequency, faster damping, a buzz component
    bad = tap(2150, 0.012, 0.15 * np.exp(-t / 0.02) * np.sin(2 * np.pi * 410 * t))
    fig, axes = plt.subplots(2, 2, figsize=(10.5, 5.6))
    for col, (name, sig, c) in enumerate([("intact tile (synthetic)", good, C_OK), ("cracked tile (synthetic)", bad, C_BAD)]):
        a = axes[0, col]
        a.plot(t * 1000, sig, color=c, lw=0.8)
        a.set_title(f"{name}\nRMS {compute_rms(sig):.3f}", fontsize=9)
        a.set_xlabel("time (ms)")
        a.set_ylabel("amplitude")
        f, m = compute_fft(sig, sr)
        pk = dominant_frequency(f, m)
        b = axes[1, col]
        b.plot(f, m, color=c, lw=0.9)
        b.set_xlim(0, 8000)
        b.axvline(pk, color=C_GREY, ls="--", lw=1)
        b.set_title(f"spectrum - peak {pk:.0f} Hz", fontsize=9)
        b.set_xlabel("frequency (Hz)")
        b.set_ylabel("magnitude")
    fig.suptitle("SYNTHETIC signals produced to show what the analysis chain measures - NOT recordings of real tiles",
                 fontsize=9.5, color=C_BAD, fontweight="bold")
    fig.tight_layout()
    save(fig, "fig_acoustic_synthetic.png")


# =============================================================================
# 9. Real frame with detections
# =============================================================================
def load_cfg():
    import yaml
    return yaml.safe_load((ROOT / "camera_node" / "python" / "camera" / "config.yaml").read_text(encoding="utf-8"))


def fig_real_frame():
    try:
        import cv2
        from camera.segmentation import locate_tiles
    except Exception as exc:
        print("skip real frame:", exc)
        return
    vp = VIDEOS / "WIN_20261011_12_16_03_Pro.mp4"
    if not vp.exists():
        print("skip real frame: no video")
        return
    cfg = load_cfg()
    cap = cv2.VideoCapture(str(vp))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 790)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return
    regions, roi = locate_tiles(frame, cfg["segmentation"])
    over = frame.copy()
    H, W = frame.shape[:2]
    if roi is not None:
        x0, y0, x1, y1 = [int(v) for v in roi]
        cv2.rectangle(over, (x0, y0), (x1, y1), (255, 200, 0), 4)
    for r in regions:
        cv2.drawContours(over, [r.contour], -1, (0, 255, 0), 4)
        x, y, w, h = r.bbox
        cv2.rectangle(over, (x, y), (x + w, y + h), (0, 160, 255), 3)
    # trigger line: horizontal, perpendicular to the belt (belt runs top->bottom in these clips)
    cv2.line(over, (0, int(H * 0.5)), (W, int(H * 0.5)), (0, 0, 255), 4)
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.9))
    axes[0].imshow(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    axes[0].set_title("raw frame (1920x1080 webcam, belt top-to-bottom)", fontsize=9)
    axes[1].imshow(cv2.cvtColor(over, cv2.COLOR_BGR2RGB))
    axes[1].set_title("pipeline view: belt ROI (cyan), tile contour (green), box (orange), trigger line (red)", fontsize=8.2)
    for a in axes:
        a.axis("off")
    fig.tight_layout()
    save(fig, "fig_real_frame.png")


# =============================================================================
# 10. Gallery + results from the 92-tile export and the 88 human labels
# =============================================================================
def human_items():
    p = DATASET / "human_labels.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))["items"]


def fig_gallery():
    import cv2
    items = {i["id"]: i for i in human_items()}
    if not items:
        print("skip gallery: no dataset")
        return
    picks = [
        ("WIN_20261011_12_16_03_t012", "intact"),
        ("WIN_20261011_12_18_55_t003", "intact"),
        ("WIN_20261011_12_16_03_t002", "snapped piece"),
        ("WIN_20261011_12_16_03_t013", "broken, corner gone"),
        ("WIN_20261011_12_16_03_t006", "chipped edge"),
        ("WIN_20261011_12_16_03_t007", "human: cracked - pipeline MISSED it"),
        ("WIN_20261011_12_25_11_t024", "pipeline MISSED (human: both)"),
        ("WIN_20261011_12_18_55_t005", "pipeline FALSE reject (human: fine)"),
    ]
    fig, axes = plt.subplots(2, 4, figsize=(11, 6.2))
    for ax, (tid, note) in zip(axes.ravel(), picks):
        img = cv2.imread(str(DATASET / "tiles" / f"{tid}.png"))
        it = items.get(tid, {})
        if img is not None:
            ax.imshow(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        ax.axis("off")
        g = it.get("grade", "?")
        ax.set_title(f"{tid.replace('WIN_20261011_', '')}\nhuman: {it.get('label')}   pipeline: {g}", fontsize=7.6,
                     color=GRADE_COL.get(g, "black"))
        ax.text(0.5, -0.03, note, transform=ax.transAxes, ha="center", va="top", fontsize=7.6)
    fig.tight_layout(h_pad=3.2)
    save(fig, "fig_tile_gallery.png")


def fig_results():
    items = human_items()
    if not items:
        return
    short = lambda v: v.replace("WIN_20261011_", "").replace("_Pro.mp4", "") if v.startswith("WIN") else "phone clip"
    videos = []
    for it in items:
        s = short(it["video"])
        if s not in videos:
            videos.append(s)
    grades = ["Grade A", "Grade B", "Reject"]
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.9))
    ax = axes[0]
    bottom = np.zeros(len(videos))
    for g in grades:
        vals = np.array([sum(1 for it in items if short(it["video"]) == v and it["grade"] == g) for v in videos])
        ax.bar(videos, vals, bottom=bottom, color=GRADE_COL[g], label=g)
        bottom += vals
    ax.set_title("Pipeline grade per video (88 labelled tiles)", fontsize=9.5)
    ax.set_ylabel("tiles")
    ax.legend(frameon=False, fontsize=8)
    ax.tick_params(axis="x", labelsize=7.5)
    ax = axes[1]
    labs = ["fine", "chipped", "cracked", "both", "unsure"]
    cnt = Counter(it["label"] for it in items)
    cols = [C_OK, C_WARN, C_BAD, "#7a1f1f", C_GREY]
    ax.bar(labs, [cnt.get(l, 0) for l in labs], color=cols)
    for i, l in enumerate(labs):
        ax.text(i, cnt.get(l, 0) + 0.8, str(cnt.get(l, 0)), ha="center", fontsize=8.5)
    ax.set_title("Human verdict on the same tiles", fontsize=9.5)
    ax.set_ylabel("tiles")
    fig.tight_layout()
    save(fig, "fig_results_grades.png")


def confusion(items):
    """Rows = human verdict class; cols = pipeline decision at export time."""
    rows = [("fine", ["fine"]), ("damaged", ["chipped", "cracked", "both"])]
    cols = ["Grade A", "Grade B", "Reject"]
    m = np.zeros((2, 3), int)
    for it in items:
        if it["label"] == "unsure":
            continue
        r = 0 if it["label"] == "fine" else 1
        m[r, cols.index(it["grade"])] += 1
    return rows, cols, m


def fig_confusion():
    items = human_items()
    if not items:
        return
    rows, cols, m = confusion(items)
    fig, ax = plt.subplots(figsize=(6.4, 3.3))
    ax.imshow(m, cmap="Blues")
    ax.set_xticks(range(3), cols)
    ax.set_yticks(range(2), [f"human: {r}" for r, _ in rows])
    for i in range(2):
        for j in range(3):
            ax.text(j, i, str(m[i, j]), ha="center", va="center", fontsize=14,
                    color="white" if m[i, j] > m.max() / 2 else "black")
    ax.set_title("Pipeline decision vs human verdict (config at export time; 'unsure' excluded)", fontsize=8.8)
    ax.spines[:].set_visible(False)
    save(fig, "fig_confusion.png")
    return m


# =============================================================================
# 11. Charts from measured data
# =============================================================================
def fig_model_tradeoff():
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    for name, acc, ms, mb in MODEL_POINTS:
        c = C_CAM if "yolo26n" in name and "INT8" not in name else (C_NET if "ViT" in name else C_GREY)
        if "INT8" in name and "yolo" in name:
            c = C_BAD
        ax.scatter(ms, acc, s=40 + mb * 1.1, color=c, alpha=0.8, edgecolor="white")
        ax.annotate(f"{name}\n{mb:.0f} MB", (ms, acc), textcoords="offset points", xytext=(8, -4), fontsize=7.5)
    ax.set_xscale("log")
    ax.set_xlabel("time per tile, ms (4 threads, log scale; dev laptop)")
    ax.set_ylabel("top-1 accuracy on 76 val images (%)")
    ax.set_ylim(30, 100)
    ax.set_title("What each tile-grade model costs (bubble area = file size)", fontsize=10)
    ax.grid(alpha=0.25)
    save(fig, "fig_model_tradeoff.png")


def fig_corner_tradeoff():
    fig, ax = plt.subplots(figsize=(6.8, 3.8))
    xs = [c[1] for c in CORNER_TRADE]
    ys = [c[2] for c in CORNER_TRADE]
    ax.plot(xs, ys, "-o", color=C_BAD)
    for (lab, x, y) in CORNER_TRADE:
        ax.annotate(lab, (x, y), textcoords="offset points", xytext=(6, 6), fontsize=8)
    ax.set_xlabel("good tiles wrongly rejected (%)")
    ax.set_ylabel("smallest chip leg caught (inches, 9 in tile)")
    ax.set_title("Corner-check operating point: a business trade-off, not a tuning detail", fontsize=9.5)
    ax.grid(alpha=0.25)
    ax.set_xlim(1, 7)
    ax.set_ylim(2, 6)
    save(fig, "fig_corner_tradeoff.png")


def fig_blur():
    fig, ax = plt.subplots(figsize=(6.8, 3.8))
    exp = np.array([1 / 64, 1 / 128, 1 / 256, 1 / 512, 1 / 1000])
    sm = BELT_PX_PER_S * exp
    labs = ["1/64 s\n(webcam auto)", "1/128", "1/256", "1/512", "1/1000"]
    ax.bar(labs, sm, color=[C_BAD, C_WARN, C_OK, C_OK, C_OK])
    for i, v in enumerate(sm):
        ax.text(i, v + 0.15, f"{v:.1f} px", ha="center", fontsize=8.5)
    ax.axhspan(1, 3, color=C_GREY, alpha=0.18)
    ax.text(4.45, 2.0, "hairline crack\nwidth 1-3 px", ha="right", va="center", fontsize=8, color=C_GREY)
    ax.set_ylabel("motion smear (px)")
    ax.set_title("Belt 435 px/s x exposure time = smear (measured belt speed)", fontsize=9.5)
    save(fig, "fig_blur.png")


def fig_readiness():
    rows = ["Camera pipeline", "Station fusion + GUI", "Acoustic DSP / analysis app", "Tap sequencer (FSM)",
            "Pick-and-place twin + protocol", "Gantry firmware", "Nano striker/belt firmware", "Dimensional station", "Database / logging"]
    cols = ["Designed", "Coded", "Unit-tested", "Run on laptop\nwith real data", "Run on target\nhardware"]
    # 2 = yes, 1 = partly, 0 = no.  Source: .CLAUDE/CLAUDE.md Project Overview + Known Technical Debt, 2026-10-12.
    M = np.array([
        [2, 2, 2, 2, 0],
        [2, 2, 2, 1, 0],
        [2, 2, 2, 1, 0],
        [2, 2, 2, 1, 0],
        [2, 2, 2, 1, 0],
        [2, 0, 0, 0, 0],
        [2, 0, 0, 0, 0],
        [1, 0, 0, 0, 0],
        [2, 2, 2, 1, 0],
    ])
    from matplotlib.colors import ListedColormap
    fig, ax = plt.subplots(figsize=(8.6, 4.6))
    ax.imshow(M, cmap=ListedColormap(["#f1d6d3", "#f6e6b8", "#cfe8d7"]), vmin=0, vmax=2, aspect="auto")
    ax.set_xticks(range(len(cols)), cols, fontsize=8)
    ax.set_yticks(range(len(rows)), rows, fontsize=8.5)
    ax.xaxis.tick_top()
    sym = {0: "no", 1: "partly", 2: "yes"}
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            ax.text(j, i, sym[M[i, j]], ha="center", va="center", fontsize=8)
    ax.spines[:].set_visible(False)
    ax.tick_params(length=0)
    save(fig, "fig_readiness.png")


def fig_timeline():
    ev = [  # start, end (YYYY-MM-DD), label, colour   -- source: documents/deliverables/logbook/Logbook.md
        ("2026-07-08", "2026-07-10", "Acoustic capture module + requirements", C_AC),
        ("2026-07-28", "2026-08-03", "UNO Q tooling, App Bricks layout", C_PP),
        ("2026-08-07", "2026-08-11", "Camera pipeline + calibration", C_CAM),
        ("2026-08-13", "2026-08-17", "Pick-place digital twin", C_PP),
        ("2026-08-19", "2026-08-26", "Review 1; ball-drop FSM; ViT/YOLO models", C_AC),
        ("2026-08-31", "2026-09-10", "Gantry control design + bench apps", C_PP),
        ("2026-09-08", "2026-09-22", "Acoustic-Analysis app v0.1-0.2", C_AC),
        ("2026-09-20", "2026-09-21", "Pi 5 move, line trigger, edge benchmark", C_CAM),
        ("2026-10-08", "2026-10-12", "Collect screen, station service, belt videos, labels", C_NET),
    ]
    import datetime as dt
    d = lambda s: dt.datetime.strptime(s, "%Y-%m-%d")
    fig, ax = plt.subplots(figsize=(10.2, 3.9))
    for i, (a, b, lab, c) in enumerate(ev):
        ax.barh(i, (d(b) - d(a)).days + 1, left=matplotlib.dates.date2num(d(a)), color=c, height=0.55)
        ax.text(matplotlib.dates.date2num(d(b)) + 2, i, lab, va="center", fontsize=8)
    ax.axvline(matplotlib.dates.date2num(d("2026-08-19")), color=C_GREY, ls="--", lw=1)
    ax.text(matplotlib.dates.date2num(d("2026-08-19")), -0.9, "Review 1", fontsize=8, ha="center", color=C_GREY)
    ax.axvline(matplotlib.dates.date2num(d("2026-10-21")), color=C_BAD, ls="--", lw=1)
    ax.text(matplotlib.dates.date2num(d("2026-10-21")), -0.9, "Review 2 (tentative)", fontsize=8, ha="center", color=C_BAD)
    ax.xaxis_date()
    ax.set_yticks([])
    ax.invert_yaxis()
    ax.set_xlim(matplotlib.dates.date2num(d("2026-07-05")), matplotlib.dates.date2num(d("2026-11-25")))
    ax.spines["left"].set_visible(False)
    fig.autofmt_xdate()
    save(fig, "fig_timeline.png")


def fig_tests():
    pts = [("2026-07-09", 11), ("2026-08-07", 26), ("2026-08-20", 57), ("2026-09-20", 124), ("2026-10-11", 462), ("2026-10-12", 734)]
    import datetime as dt
    x = [dt.datetime.strptime(a, "%Y-%m-%d") for a, _ in pts]
    y = [b for _, b in pts]
    fig, ax = plt.subplots(figsize=(6.8, 3.4))
    ax.plot(x, y, "-o", color=C_CAM)
    for xi, yi in zip(x, y):
        ax.annotate(str(yi), (xi, yi), textcoords="offset points", xytext=(0, 7), ha="center", fontsize=8)
    ax.set_ylabel("automated tests passing")
    ax.set_title("Test suite growth (logbook + final run 2026-10-12)", fontsize=9.5)
    ax.grid(alpha=0.25)
    fig.autofmt_xdate()
    save(fig, "fig_tests.png")


def main():
    fig_architecture()
    fig_method()
    fig_camera_pipeline()
    fig_decision_flow()
    fig_tap_fsm()
    fig_sequence()
    fig_grade_fusion()
    fig_acoustic_synthetic()
    for f in (fig_real_frame, fig_gallery, fig_results, fig_confusion):
        try:
            f()
        except Exception as exc:  # keep going; one missing data file must not stop the rest
            print("skip", f.__name__, "->", exc)
    fig_model_tradeoff()
    fig_corner_tradeoff()
    fig_blur()
    fig_readiness()
    fig_timeline()
    fig_tests()


if __name__ == "__main__":
    main()
