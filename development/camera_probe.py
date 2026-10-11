"""What does THIS camera actually do? Measured, not assumed.

    python development\\camera_probe.py                  # camera 0, DirectShow
    python development\\camera_probe.py --index 1 --api dshow --belt-speed 435

It briefly turns the camera on (about a minute) and prints:

  1. FORMATS   - for each resolution / pixel format, the frame rate the camera really
                 delivers and what the driver reports back. Asking for 1080p and getting
                 640x480, or 30 fps and getting 8, shows up here.
  2. EXPOSURE  - with the exposure set by hand (DirectShow codes: -6 = 1/64 s, -7 = 1/128
                 s ...), how bright the image is and how many frames per second the camera
                 still delivers. A fast shutter freezes motion but starves the sensor of
                 light; too long a shutter drops the frame rate (a dark room's auto
                 exposure does this on its own).
  3. GAIN      - whether the driver accepts a gain change at all (many webcams reject it,
                 so a darker, faster-shutter image cannot be brightened in software).
  4. BLUR      - the motion smear each exposure would cause at `--belt-speed` px/s, i.e.
                 speed x exposure time. A hairline crack is 1-3 px wide.

What it found on the project's laptop webcam (2026-10-11): ~30 fps at every resolution
up to 1920x1080; auto exposure 1/64 s (~7 px of smear at the sample belt's 435 px/s);
manual 1/128 s gave a nearly black image (brightness 26/255) in ordinary room light; the
driver rejected every gain change. So a sharper picture needs MORE LIGHT on the belt (about
8x for 1/256 s), not a software setting.

Nothing here changes config.yaml; use the numbers to set camera.exposure / camera.frame_*.
"""

from __future__ import annotations

import argparse
import sys
import time

import cv2
import numpy as np

APIS = {"any": cv2.CAP_ANY, "dshow": getattr(cv2, "CAP_DSHOW", cv2.CAP_ANY),
        "msmf": getattr(cv2, "CAP_MSMF", cv2.CAP_ANY), "v4l2": getattr(cv2, "CAP_V4L2", cv2.CAP_ANY)}


def measure(cap, seconds: float = 2.0) -> tuple[float, float]:
    """(frames per second actually delivered, mean brightness 0-255)."""
    for _ in range(5):
        cap.read()  # the first frames are often stale or dark
    n, t0, bright = 0, time.monotonic(), []
    while time.monotonic() - t0 < seconds:
        ok, frame = cap.read()
        if ok:
            n += 1
            if n % 5 == 0:
                bright.append(float(frame.mean()))
    return n / (time.monotonic() - t0), float(np.mean(bright)) if bright else float("nan")


def open_camera(index: int, api: int, width: int, height: int, fourcc: str | None, fps: int = 30):
    cap = cv2.VideoCapture(index, api)
    if fourcc:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def fourcc_of(cap) -> str:
    v = int(cap.get(cv2.CAP_PROP_FOURCC))
    return "".join(chr((v >> 8 * i) & 0xFF) for i in range(4)) if v > 0 else "?"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--index", type=int, default=0, help="camera index (default 0)")
    ap.add_argument("--api", choices=sorted(APIS), default="dshow", help="OpenCV backend (default dshow; v4l2 on Linux)")
    ap.add_argument("--belt-speed", type=float, default=435.0, help="how fast tiles cross the frame, px/s, for the blur estimate (default 435)")
    args = ap.parse_args()
    api = APIS[args.api]

    print(f"Camera {args.index} via {args.api}. The camera light will come on.\n")
    print("--- 1. FORMATS (asked -> got, delivered frame rate)")
    for w, h, fc in [(640, 480, None), (1280, 720, None), (1280, 720, "MJPG"), (1920, 1080, None), (1920, 1080, "MJPG")]:
        cap = open_camera(args.index, api, w, h, fc)
        if not cap.isOpened():
            print(f"  {w}x{h} {fc or 'default':8s}: could not open")
            cap.release()
            continue
        fps, bright = measure(cap)
        print(f"  asked {w}x{h} {fc or 'default':8s} -> got {int(cap.get(3))}x{int(cap.get(4))} {fourcc_of(cap)}  "
              f"delivered {fps:5.1f} fps  brightness {bright:5.1f}")
        cap.release()

    if args.api not in ("dshow", "v4l2"):
        print(f"\n(exposure and gain control are only known for dshow / v4l2; skipping them for {args.api})")
        return
    manual, auto = (0.25, 0.75) if args.api == "dshow" else (1.0, 3.0)

    print("\n--- 2. EXPOSURE (manual; 1280x720)  and  4. the motion smear it would cause")
    cap = open_camera(args.index, api, 1280, 720, "MJPG")
    fps, bright = measure(cap, 1.5)
    print(f"  automatic: exposure reads {cap.get(cv2.CAP_PROP_EXPOSURE)}, {fps:.1f} fps, brightness {bright:.0f}")
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, manual)
    values = (-3, -4, -5, -6, -7, -8, -9) if args.api == "dshow" else (500, 200, 100, 50, 20, 10, 5)
    for e in values:
        cap.set(cv2.CAP_PROP_EXPOSURE, e)
        time.sleep(0.4)
        fps, bright = measure(cap, 1.2)
        seconds = 2.0 ** e if args.api == "dshow" else e * 1e-4
        print(f"  exposure {e:>4} (1/{1 / seconds:5.0f} s): {fps:5.1f} fps, brightness {bright:5.1f}   smear at {args.belt_speed:.0f} px/s = {args.belt_speed * seconds:5.1f} px")

    print("\n--- 3. GAIN")
    accepted = [g for g in (1, 32, 64, 128, 255) if cap.set(cv2.CAP_PROP_GAIN, g) and cap.get(cv2.CAP_PROP_GAIN) == g]
    print(f"  gain values the driver accepted: {accepted or 'none (it rejects gain changes)'}")
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, auto)  # leave the camera as it was found
    cap.release()


if __name__ == "__main__":
    sys.exit(main())
