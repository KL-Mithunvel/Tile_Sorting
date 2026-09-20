"""Measure what the tile-grade models actually cost to run, per tile.

Designed to produce numbers that mean the same thing on the dev laptop and on
the camera station's Raspberry Pi 5, so run it on both and compare:

    python camera_models/cam_edge/benchmark.py
    python camera_models/cam_edge/benchmark.py --report bench_pi.json

It measures three separate things, because lumping them together is how people
end up optimising the wrong one:

  1. MODEL COMPUTE      — forward pass only, synthetic input, swept over
                          thread counts. This is the number that scales with
                          the CPU.
  2. PREPROCESSING      — decode/resize/normalize one tile crop. Small, but
                          it is per-tile work the model number hides.
  3. PIPELINE STAGES    — what camera_node already does per frame and per
                          tile without any model at all (HSV segmentation,
                          crack detection, corner detection, JPEG encode for
                          the MJPEG stream). This is the context that decides
                          whether a model is "heavy" or not.

Then it puts them together into a per-frame and per-tile budget and reports
the sustainable frame rate, which is the thing that actually matters for the
station.

Nothing here needs torch, ultralytics or transformers — only onnxruntime,
opencv, numpy, psutil. That is deliberate: it must be installable on the Pi
without the training stack.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np
import yaml

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
# camera_node's pure modules are the thing we're budgeting against.
sys.path.insert(0, str(REPO_ROOT / "camera_node" / "python"))
sys.path.insert(0, str(HERE))

import cv2  # noqa: E402

from runtime import OnnxClassifier, preprocess, quantize_int8  # noqa: E402


def load_config(path: Path | None = None) -> dict:
    with open(path or HERE / "config.yaml", "r") as f:
        return yaml.safe_load(f)


def _percentiles(samples_ms: list[float]) -> dict:
    s = sorted(samples_ms)
    n = len(s)
    return {
        "mean_ms": round(statistics.fmean(s), 3),
        "p50_ms": round(s[n // 2], 3),
        "p90_ms": round(s[min(n - 1, int(n * 0.90))], 3),
        "p95_ms": round(s[min(n - 1, int(n * 0.95))], 3),
        "max_ms": round(s[-1], 3),
    }


def time_it(fn, warmup: int, iters: int) -> dict:
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return _percentiles(samples)


def rss_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / 1e6
    except Exception:
        return float("nan")


def platform_info() -> dict:
    import onnxruntime as ort

    info = {
        "machine": platform.machine(),
        "processor": platform.processor() or platform.uname().processor,
        "system": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "onnxruntime": ort.__version__,
        "opencv": cv2.__version__,
    }
    # /proc/cpuinfo's "model name" is how you actually identify a Pi's SoC;
    # platform.processor() returns something useless like "aarch64" there.
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith(("model name", "Model")):
                    info["cpu"] = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    try:
        import os

        info["cpu_count"] = os.cpu_count()
    except Exception:
        pass
    return info


# ---------------------------------------------------------------------------
# 0. CPU reference
# ---------------------------------------------------------------------------
def bench_cpu_reference() -> dict:
    """Achieved GFLOP/s on a dense fp32 matmul, via whatever BLAS numpy has.

    This exists so a run on the dev laptop and a run on the Pi can be related
    to each other WITHOUT hand-waving a "the Pi is about N times slower"
    factor. These models are dominated by dense GEMM, so the ratio of this
    number between two machines is a reasonable scaling factor for everything
    else in this file.

    **Caveat, read before quoting it:** numpy's BLAS picks its own thread
    count here and we do not pin it (threadpoolctl is not a dependency), so
    this is a whole-machine figure, not a per-core one. On a 22-thread x86
    laptop it reads ~200 GFLOP/s, which is above any single core's peak.
    For a per-CORE architecture comparison use the 1-thread model latencies
    in the MODEL COMPUTE table below instead — those are pinned properly via
    ONNX Runtime's intra_op_num_threads and are the honest cross-platform
    number.
    """
    n = 512
    a = np.random.rand(n, n).astype(np.float32)
    bm = np.random.rand(n, n).astype(np.float32)
    flops = 2.0 * n ** 3
    for _ in range(3):
        a @ bm
    samples = []
    for _ in range(10):
        t0 = time.perf_counter()
        a @ bm
        samples.append(time.perf_counter() - t0)
    best = min(samples)
    gflops = flops / best / 1e9
    print(f"  fp32 matmul {n}x{n}         {gflops:8.1f} GFLOP/s  ({best*1000:.2f} ms)"
          f"   [BLAS threads unpinned - whole machine, not per-core]")
    return {"matmul_n": n, "gflops": round(gflops, 1), "best_ms": round(best * 1000, 3)}


# ---------------------------------------------------------------------------
# 1. Model compute
# ---------------------------------------------------------------------------
def bench_models(cfg: dict) -> list[dict]:
    b = cfg["benchmark"]
    results = []

    entries = []
    for m in cfg["models"]:
        entries.append((m["name"], HERE / m["onnx"], m["preprocess"], m.get("note", ""), "fp32"))

    if cfg.get("quantize", {}).get("enabled"):
        out_dir = HERE / cfg["quantize"]["output_dir"]
        for m in cfg["models"]:
            src = HERE / m["onnx"]
            if not src.exists():
                continue
            dst = out_dir / f"{m['name']}_int8.onnx"
            if not dst.exists():
                print(f"  quantizing {m['name']} -> INT8 ...", flush=True)
                if quantize_int8(src, dst) is None:
                    print("    onnxruntime.quantization unavailable, skipping")
                    continue
            entries.append((f"{m['name']} INT8", dst, m["preprocess"], "", "int8"))

    for name, onnx_path, pre_mode, note, precision in entries:
        if not onnx_path.exists():
            print(f"  SKIP {name}: missing {onnx_path}")
            continue

        size_mb = onnx_path.stat().st_size / 1e6
        row = {
            "model": name,
            "precision": precision,
            "onnx_mb": round(size_mb, 1),
            "note": note,
            "threads": {},
        }

        for threads in b["thread_counts"]:
            before = rss_mb()
            clf = OnnxClassifier(
                onnx_path, cfg["classes"], pre_mode, threads=threads
            )
            inp = clf.session.get_inputs()[0]
            shape = [1 if (isinstance(d, str) or d is None) else d for d in inp.shape]
            x = np.random.rand(*shape).astype(np.float32)

            stats = time_it(lambda: clf.predict_tensor(x), b["warmup_iters"], b["measure_iters"])
            stats["rss_delta_mb"] = round(rss_mb() - before, 1)
            stats["throughput_per_s"] = round(1000.0 / stats["p50_ms"], 1)
            row["threads"][str(threads)] = stats
            del clf

        results.append(row)
        t4 = row["threads"].get(str(b["thread_counts"][-1]), {})
        print(
            f"  {name:24s} {size_mb:7.1f} MB  "
            f"p50 {t4.get('p50_ms', float('nan')):8.2f} ms  "
            f"({t4.get('throughput_per_s', 0):7.1f} img/s @ {b['thread_counts'][-1]}T)",
            flush=True,
        )

    return results


# ---------------------------------------------------------------------------
# 2. Preprocessing
# ---------------------------------------------------------------------------
def bench_preprocess(cfg: dict) -> dict:
    b = cfg["benchmark"]
    # A tile crop is roughly square and a few hundred px — this is the shape
    # segment_tile() hands over, not a full frame.
    crop = (np.random.rand(300, 300, 3) * 255).astype(np.uint8)
    out = {}
    for mode in sorted({m["preprocess"] for m in cfg["models"]}):
        out[mode] = time_it(
            lambda mode=mode: preprocess(crop, mode), b["warmup_iters"], b["measure_iters"]
        )
        print(f"  preprocess ({mode:8s})      p50 {out[mode]['p50_ms']:8.3f} ms", flush=True)
    return out


# ---------------------------------------------------------------------------
# 3. camera_node pipeline stages (no model involved)
# ---------------------------------------------------------------------------
def bench_pipeline(cfg: dict) -> dict:
    """Cost of what the station already does, so the model can be judged
    against it rather than in a vacuum."""
    from camera.capture import load_config as load_camera_config
    from camera.corner_detection import detect_broken_corner
    from camera.crack_detection import detect_cracks
    from camera.segmentation import segment_tile

    b = cfg["benchmark"]
    cam_cfg = load_camera_config()
    seg = cam_cfg["segmentation"]
    crack = cam_cfg["crack_detection"]
    corner = cam_cfg["corner_detection"]

    w, h = b["frame_width"], b["frame_height"]

    # Synthetic frame with a terracotta-ish tile on a dark background, chosen
    # to land inside config.yaml's calibrated HSV range so segmentation does
    # real work rather than bailing out early on an empty mask.
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = (20, 20, 20)
    tile_bgr_color = (60, 110, 190)  # B,G,R — a brown/terracotta tone
    cv2.rectangle(frame, (w // 2 - 130, h // 2 - 130), (w // 2 + 130, h // 2 + 130),
                  tile_bgr_color, -1)
    noise = np.random.randint(-12, 12, frame.shape, dtype=np.int16)
    frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)

    out = {}

    region = segment_tile(frame, seg["hsv_lower"], seg["hsv_upper"],
                          seg["min_tile_area_px"], seg["morph_kernel_size"])
    if region is None:
        out["_warning"] = ("synthetic tile did not segment — stage timings below are "
                           "for the early-exit path, not a real tile")

    out["segment_tile_per_frame"] = time_it(
        lambda: segment_tile(frame, seg["hsv_lower"], seg["hsv_upper"],
                             seg["min_tile_area_px"], seg["morph_kernel_size"]),
        b["warmup_iters"], b["measure_iters"])

    encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), b["jpeg_quality"]]
    out["jpeg_encode_per_frame"] = time_it(
        lambda: cv2.imencode(".jpg", frame, encode_params),
        b["warmup_iters"], b["measure_iters"])

    if region is not None:
        tile = region.cropped_bgr
        out["detect_cracks_per_tile"] = time_it(
            lambda: detect_cracks(tile, crack["canny_low"], crack["canny_high"],
                                  crack["min_crack_length_px"], crack["min_aspect_ratio"],
                                  crack["minor_severity_max_length_px"],
                                  crack["blur_kernel_size"],
                                  crack["border_margin_px"]),
            b["warmup_iters"], b["measure_iters"])
        out["detect_broken_corner_per_tile"] = time_it(
            lambda: detect_broken_corner(region.contour, corner["min_fill_ratio"],
                                         corner["max_missing_extent_fraction"],
                                         corner["tile_size_inches"]),
            b["warmup_iters"], b["measure_iters"])

    for k, v in out.items():
        if isinstance(v, dict):
            print(f"  {k:32s} p50 {v['p50_ms']:8.3f} ms", flush=True)
    return out


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------
def build_budget(models: list[dict], pre: dict, pipe: dict, cfg: dict) -> dict:
    """Per-frame and per-tile totals, and the frame rate they imply."""
    threads = str(cfg["benchmark"]["thread_counts"][-1])

    def p50(d, key):
        return d.get(key, {}).get("p50_ms", 0.0) if isinstance(d.get(key), dict) else 0.0

    per_frame = p50(pipe, "segment_tile_per_frame") + p50(pipe, "jpeg_encode_per_frame")
    per_tile_cv = p50(pipe, "detect_cracks_per_tile") + p50(pipe, "detect_broken_corner_per_tile")

    rows = []
    for m in models:
        t = m["threads"].get(threads, {})
        infer = t.get("p50_ms", 0.0)
        pre_ms = pre.get("yolo_cls" if "yolo" in m["model"] else "vit_hf", {}).get("p50_ms", 0.0)
        total_tile = per_tile_cv + pre_ms + infer
        rows.append({
            "model": m["model"],
            "onnx_mb": m["onnx_mb"],
            "infer_p50_ms": round(infer, 2),
            "preprocess_p50_ms": round(pre_ms, 3),
            "per_tile_total_ms": round(total_tile, 2),
            # Every frame pays the per-frame cost; only a departing tile pays
            # the per-tile cost, so the worst frame is the one a tile leaves on.
            "worst_frame_ms": round(per_frame + total_tile, 2),
            "worst_frame_fps": round(1000.0 / (per_frame + total_tile), 1)
            if (per_frame + total_tile) > 0 else None,
        })

    return {
        "threads": int(threads),
        "per_frame_fixed_ms": round(per_frame, 2),
        "per_frame_fixed_fps": round(1000.0 / per_frame, 1) if per_frame > 0 else None,
        "per_tile_classical_cv_ms": round(per_tile_cv, 2),
        "models": rows,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Benchmark tile-grade models for edge deployment.")
    ap.add_argument("--report", type=str, default=None, help="Write full results as JSON here.")
    ap.add_argument("--config", type=str, default=None, help="Alternate config.yaml.")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config) if args.config else None)
    info = platform_info()

    print("=" * 78)
    print("PLATFORM")
    for k, v in info.items():
        print(f"  {k:14s} {v}")

    print("\nCPU REFERENCE (relates this machine to another; see docstring)")
    cpu_ref = bench_cpu_reference()

    print("\nMODEL COMPUTE (forward pass only, synthetic input)")
    models = bench_models(cfg)

    print("\nPREPROCESSING (one 300x300 tile crop -> NCHW tensor)")
    pre = bench_preprocess(cfg)

    print(f"\ncamera_node PIPELINE STAGES "
          f"({cfg['benchmark']['frame_width']}x{cfg['benchmark']['frame_height']}, no model)")
    pipe = bench_pipeline(cfg)

    budget = build_budget(models, pre, pipe, cfg)

    print("\n" + "=" * 78)
    print(f"BUDGET @ {budget['threads']} threads")
    print(f"  Every frame pays: {budget['per_frame_fixed_ms']:.2f} ms "
          f"(segment + JPEG encode) -> {budget['per_frame_fixed_fps']} fps ceiling with no model")
    print(f"  Each departing tile also pays: "
          f"{budget['per_tile_classical_cv_ms']:.2f} ms of classical CV (crack + corner)")
    print()
    print(f"  {'model':24s} {'MB':>7s} {'infer':>9s} {'per-tile':>10s} {'worst frame':>12s} {'fps':>7s}")
    for r in budget["models"]:
        print(f"  {r['model']:24s} {r['onnx_mb']:7.1f} {r['infer_p50_ms']:8.2f}m "
              f"{r['per_tile_total_ms']:9.2f}m {r['worst_frame_ms']:11.2f}m "
              f"{r['worst_frame_fps']:7.1f}")
    print("=" * 78)

    if args.report:
        payload = {
            "platform": info,
            "cpu_reference": cpu_ref,
            "models": models,
            "preprocess": pre,
            "pipeline": pipe,
            "budget": budget,
        }
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2))
        print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
