"""Draws the whole-project flow chart: physical line, per-station pipelines, fusion,
pick-and-place, the optional master PC, and the offline/dev tooling around them.

Run from the repo root:
    .venv\\Scripts\\python.exe documents\\deliverables\\project_overview\\make_project_flowchart.py

Output: project_overview_flowchart.png (+ .svg) next to this script.
Content mirrors .claude/CLAUDE.md and documents/project/Automation_Architecture.md.
Dashed outline = written and laptop-tested, never run on the real hardware.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parent

C_CAM, C_AC, C_PP = "#2f6fb0", "#c0562b", "#3b8a5a"
C_FUS, C_MASTER, C_DEV, C_LINE = "#6a4c93", "#8a8a8a", "#b08a1e", "#444444"
INK = "#1d1d1f"

fig, ax = plt.subplots(figsize=(20, 14), dpi=150)
ax.set_xlim(0, 200)
ax.set_ylim(0, 140)
ax.axis("off")
fig.patch.set_facecolor("white")


def box(cx, cy, w, h, text, color, solid=False, fs=9.5, bold=False):
    p = FancyBboxPatch((cx - w / 2, cy - h / 2), w, h,
                       boxstyle="round,pad=0.2,rounding_size=1.2",
                       fc=color + ("22" if not solid else "22"), ec=color, lw=1.8,
                       ls="-" if solid else (0, (4, 2)))
    ax.add_patch(p)
    ax.text(cx, cy, text, ha="center", va="center", fontsize=fs, color=INK,
            fontweight="bold" if bold else "normal", linespacing=1.35)
    return cx, cy, w, h


def arrow(a, b, side_a="b", side_b="t", color=INK, dashed=False, rad=0.0):
    def pt(bx, s):
        cx, cy, w, h = bx
        return {"t": (cx, cy + h / 2), "b": (cx, cy - h / 2),
                "l": (cx - w / 2, cy), "r": (cx + w / 2, cy)}[s]
    ax.add_patch(FancyArrowPatch(pt(a, side_a), pt(b, side_b), arrowstyle="-|>",
                                 mutation_scale=14, lw=1.6, color=color,
                                 ls=(0, (4, 3)) if dashed else "-",
                                 connectionstyle=f"arc3,rad={rad}", shrinkA=1, shrinkB=1))


def lane(x0, y0, x1, y1, title, color):
    ax.add_patch(FancyBboxPatch((x0, y0), x1 - x0, y1 - y0,
                                boxstyle="round,pad=0.3,rounding_size=2",
                                fc=color + "0c", ec=color, lw=1.2))
    if title:
        ax.text(x1 - 1.5, y1 - 1.8, title, fontsize=11, fontweight="bold", color=color,
                va="top", ha="right")


ax.text(100, 138, "Automated Tile Inspection, Grading, Sorting and Packing - project pipeline",
        ha="center", fontsize=17, fontweight="bold", color=INK)
ax.text(100, 134.6, "Sree Murugan Tile Works proof of concept  |  VIT Chennai BMEE497J/BMHA497J Project 1  |  "
        "Phase 1: laboratory proof of concept", ha="center", fontsize=10.5, color="#555555")

# ---- physical line input -------------------------------------------------
belt = box(64, 127, 100, 6, "Tiles arrive on the conveyor belt  (DC belt, PWM from the Arduino Nano)\n"
           "belt speed is open-loop; the Nano stops the belt itself on a Pi-silence watchdog",
           C_LINE, solid=True, fs=9.5, bold=True)

# ---- camera lane ----------------------------------------------------------
lane(3, 52, 62, 121, "CAMERA STATION  |  Pi 5", C_CAM)
cam = [box(32, 111, 50, 6.5, "1  Capture frame\nUSB webcam | CSI Picamera2 | video file", C_CAM),
       box(32, 100, 50, 6.5, "2  Belt ROI + HSV segmentation\nshape gate rejects non-tiles", C_CAM),
       box(32, 89, 50, 6.5, "3  Region tracker + line-crossing trigger\npicks the sharpest frame per tile", C_CAM),
       box(32, 78, 50, 6.5, "4  Crack check (Canny)  +  broken-corner check\nfill ratio, notch depth, aspect", C_CAM),
       box(32, 66, 50, 7.5, "5  Rule grade A / B / Reject\n+ optional ONNX grade model (worse-of,\nabstains below 0.6 confidence)", C_CAM),
       box(32, 56, 50, 4.8, "Camera result + tile id  ->  MQTT  station/camera", C_CAM, solid=True, fs=9)]
for a, b in zip(cam, cam[1:]):
    arrow(a, b)

# ---- acoustic lane --------------------------------------------------------
lane(66, 52, 125, 121, "ACOUSTIC  |  Nano + Pi", C_AC)
ac = [box(95.5, 111, 50, 6.5, "1  ToF / laser sees tile arrive\n(Nano; fresh read at the strike - none, no strike)", C_AC),
      box(95.5, 100, 50, 6.5, "2  Tap sequence: settle -> ARM -> RELEASE\nball-drop impactor, checksummed serial", C_AC),
      box(95.5, 89, 50, 6.5, "3  Microphone capture window\npre-trigger buffer, hardware-triggered", C_AC),
      box(95.5, 78, 50, 6.5, "4  Features: FFT peak, centroid, SNR, decay\nmachine-noise removal", C_AC),
      box(95.5, 66, 50, 7.5, "5  Sound grade vs reference profile\n(null today: abstains, does not vote\nuntil calibrated on real tiles)", C_AC),
      box(95.5, 56, 50, 4.8, "Tap result + tile_present  ->  MQTT  station/tap", C_AC, solid=True, fs=9)]
for a, b in zip(ac, ac[1:]):
    arrow(a, b)

arrow(belt, cam[0], "b", "t", rad=0.1)
arrow(belt, ac[0], "b", "t", rad=-0.1)

# ---- fusion ---------------------------------------------------------------
lane(3, 28, 125, 47, "DECISION SERVICE  |  Pi 5", C_FUS)
ledger = box(30, 36, 45, 8, "TileLedger (FIFO)\nbinds each tap to the oldest\nwaiting camera tile; timeouts, orphans", C_FUS)
fuse = box(77, 36, 40, 8, "fuse_final()\nworse of camera and sound\nGrade A / B / C / Reject / Hold", C_FUS)
store = box(112, 36, 22, 8, "SQLite store\none row per tile\n+ operator GUI :5001", C_FUS)
arrow(ledger, fuse, "r", "l")
arrow(fuse, store, "r", "l")
arrow(cam[-1], ledger, "b", "t")
arrow(ac[-1], ledger, "b", "t", rad=0.15)

# ---- pick and place -------------------------------------------------------
lane(3, 3, 125, 25, "", C_PP)
ax.text(123.5, 23.2, "PICK AND PLACE  |  UNO Q", fontsize=11, fontweight="bold", color=C_PP,
        va="top", ha="right")
pp = [box(17, 12, 24, 11, "FeedClient + FinalQueue\nFIFO, de-dup, ack,\nback-pressure", C_PP),
      box(44, 12, 24, 11, "JobRunner\nsequencer + coordinate\nmodel; 1 retry on miss", C_PP),
      box(71, 12, 24, 11, "ASCII line protocol\nserial, checksummed,\nping watchdog", C_PP),
      box(97, 12, 24, 11, "Gantry firmware core\nXYZ steppers, homing,\nvacuum, closed loop", C_PP),
      box(117, 12, 14, 11, "Sorted\nstacks / bins\nby grade", C_PP, solid=True)]
for a, b in zip(pp, pp[1:]):
    arrow(a, b, "r", "l")
ax.add_patch(FancyArrowPatch((77, 32), (17, 17.5), arrowstyle="-|>", mutation_scale=14, lw=1.6,
                             color=C_FUS, connectionstyle="arc3,rad=0.0", shrinkA=1, shrinkB=1))
ax.text(82, 26.4, "final grade over MQTT  tile/<id>/final", fontsize=9, color=C_FUS, va="center",
        ha="left", style="italic")
ax.text(4.5, 4.2, "placed / status / alarm are reported back to the Pi", fontsize=8.5,
        color=C_PP, ha="left", style="italic")

# ---- master PC ------------------------------------------------------------
lane(130, 52, 197, 121, "", C_MASTER)
ax.text(131.5, 119.2, "OPTIONAL MASTER PC  (master_node/)", fontsize=11, fontweight="bold",
        color=C_MASTER, va="top")
m = [box(163.5, 108, 58, 7, "Observer / collector\nown SQLite + CSV, health history", C_MASTER),
     box(163.5, 97, 58, 7, "Plant 3D view  |  Analysis  |  Troubleshoot\nInsights (rule-based; AI explainer parked)", C_MASTER),
     box(163.5, 86, 58, 7, "Commands: stop/start line, pause/resume feed,\ngantry pause (soft stop, NOT an e-stop)", C_MASTER),
     box(163.5, 75, 58, 7, "Modes: --demo (pretends),\n--sim (real coordinator + simulated machine)", C_MASTER)]
for a, b in zip(m, m[1:]):
    arrow(a, b)
ax.text(163.5, 60, "The line runs without it.\nTalks to the Pi broker over MQTT only.",
        ha="center", fontsize=9.5, color=C_MASTER, style="italic")
ax.add_patch(FancyArrowPatch((123.5, 38), (134.5, 52), arrowstyle="-|>", mutation_scale=14, lw=1.6,
                             color=C_MASTER, ls=(0, (4, 3)), connectionstyle="arc3,rad=-0.2"))
ax.text(126, 49.5, "MQTT status", fontsize=8.5, color=C_MASTER, ha="center", style="italic")

# ---- dev tooling (offline) ------------------------------------------------
lane(130, 3, 197, 44, "", C_DEV)
ax.text(131.5, 42.2, "OFFLINE / DEV TOOLING  (not imported by any node)", fontsize=11,
        fontweight="bold", color=C_DEV, va="top")
box(147, 32, 30, 11, "development/\nthreshold tuner, review sheet,\ndataset export, regression", C_DEV, solid=True, fs=8.8)
box(181, 32, 30, 11, "camera_models/\ncam_yolo, cam_vit train;\ncam_edge benchmarks + stages", C_DEV, solid=True, fs=8.8)
box(147, 15, 30, 11, "Acoustic-Analysis/ (submodule)\nrecord, slice, label taps,\nexport feature dataset", C_DEV, solid=True, fs=8.8)
box(181, 15, 30, 11, "documents/\ncharter, architecture,\nrequirements, deliverables", C_DEV, solid=True, fs=8.8)
ax.text(163.5, 5.8, "Only thresholds and a staged .onnx cross into the nodes, by hand.",
        ha="center", fontsize=8.8, color=C_DEV, style="italic")

# ---- legend ---------------------------------------------------------------
ax.add_patch(FancyBboxPatch((150.2, 129.2), 3.2, 2, boxstyle="round,pad=0.1", fc="#ffffff",
                            ec=INK, lw=1.6, ls=(0, (4, 2))))
ax.text(154.5, 130.2, "dashed = written and laptop-tested,\nnever run on real hardware", fontsize=8.8, va="center")
ax.add_patch(FancyBboxPatch((150.2, 124.2), 3.2, 2, boxstyle="round,pad=0.1", fc="#ffffff",
                            ec=INK, lw=1.6))
ax.text(154.5, 125.2, "solid = message, artefact or\nexternal input", fontsize=8.8, va="center")

fig.savefig(OUT / "project_overview_flowchart.png", bbox_inches="tight")
fig.savefig(OUT / "project_overview_flowchart.svg", bbox_inches="tight")
print("wrote", OUT / "project_overview_flowchart.png")
