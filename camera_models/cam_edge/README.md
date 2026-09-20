# cam_edge/

What each trained tile-grade model **costs to run** on the camera station's
Raspberry Pi 5, measured rather than assumed — speed, memory, and accuracy on one
common held-out split, for the exact `.onnx` artifact that would be deployed.

Dev-only tooling, like `cam_yolo/` and `cam_vit/` and `development/`. Not an App Bricks
node, not wired into `camera_node`'s live pipeline. Unlike those two, this folder
**trains nothing** — it only measures what they produced.

Depends on onnxruntime + opencv + numpy + Pillow + psutil. Deliberately **not** torch,
ultralytics or transformers, so the identical script runs on the Pi without the training
stack installed.

| File | Role |
|---|---|
| `config.yaml` | Which artifacts to measure, thread counts, val split, quantization toggle. |
| `runtime.py` | `OnnxClassifier` — loads an ONNX model, preprocesses a BGR tile crop, returns grade + confidence. This is the piece that would move into `camera_node` if a model is ever deployed. |
| `benchmark.py` | Speed/memory. Model compute swept over thread counts, preprocessing cost, `camera_node`'s existing per-frame/per-tile CV cost, and a combined budget. |
| `evaluate.py` | Accuracy of every artifact on one common val split, with Wilson 95% intervals and per-class recall. |
| `results/` | Committed JSON from both scripts (small, and the whole point is the record). |
| `runs/int8/` | Generated INT8 copies. Not committed. |

```bash
python camera_models/cam_edge/benchmark.py --report results/bench_devlaptop.json
python camera_models/cam_edge/evaluate.py  --report results/accuracy.json
# then, on the Pi, the identical commands with a different report name
```

## Results — dev laptop, 2026-09-20

Core Ultra 7 155H, ONNX Runtime 1.29.0 CPU, 4 threads (the Pi 5 also has 4 cores).
Accuracy on the **76-image common val split** (`cam_yolo/dataset/val`, the original
non-augmented dataset, seed 42).

| Model | ONNX | Accuracy | 95% CI | 1 thread | 4 threads | Per-tile total |
|---|---|---|---|---|---|---|
| `yolo26n-cls` | 6.2 MB | 82.9% | [72.9, 89.7] | 3.95 ms | **1.59 ms** | 2.67 ms |
| `yolo26s-cls` | 21.8 MB | **85.5%** | [75.9, 91.7] | 14.41 ms | 4.16 ms | 5.68 ms |
| `vit-base-p16-224` | 343.5 MB | **92.1%** | [83.8, 96.3] | 350.5 ms | 110.2 ms | 97.5 ms |
| `vit-base` INT8 | 87.1 MB | **92.1%** | [83.8, 96.3] | 164.2 ms | 48.7 ms | 59.7 ms |
| `yolo26n-cls` INT8 | 1.6 MB | 57.9% | [46.7, 68.3] | 64.3 ms | 19.1 ms | — |
| `yolo26s-cls` INT8 | 5.6 MB | 36.8% | [26.9, 48.1] | 234.4 ms | 64.7 ms | — |

`camera_node`'s existing work, for scale — **every frame** pays 1.34 ms (HSV
segmentation 0.75 + JPEG encode for the MJPEG stream 0.59), and **each departing tile**
pays a further 0.49 ms of classical CV (crack 0.22 + corner 0.26). With no model at all
the pipeline ceiling is ~750 fps on this machine.

Run-to-run p50 varies ~10–15% on a laptop (thermals, background load); two runs of the
ViT gave 95.6 and 110.2 ms. Treat one significant figure as real.

## Four things worth knowing

**1. Dynamic INT8 destroys the YOLO models — badly, in both directions.** It is 12×
*slower* (1.59 → 19.1 ms) *and* drops accuracy from 82.9% to 57.9%. On `yolo26s-cls`
INT8 it is worse still: class 3B recall goes to **0%** — the model stops predicting that
grade at all. ONNX Runtime inserts per-op quantize/dequantize that costs more than the
conv work it saves on a net this small, and dynamic per-tensor scales are too coarse for
these weights. Do not quantize the small models without measuring. (Static quantization
with a calibration set might behave differently; it has not been tried.)

**2. INT8 on the ViT is free accuracy-wise and halves the cost.** 343 MB → 87 MB,
110 → 49 ms, and accuracy is **unchanged** at 92.1% — the same 70/76 images. That is the
one quantization that clearly pays.

**3. Preprocessing is worth more than model choice — and it is easy to get silently
wrong.** The first version of `runtime.py` used `cv2.resize(..., INTER_LINEAR)` and
scored `yolo26n-cls` at 75.0%, an 8-point loss, with no error raised anywhere. The cause:
PIL's BILINEAR is antialiased on downscale and `cv2.INTER_LINEAR` is not, and these are
large photos downscaled hard to 224. Measured on the 76-image split:

| Resize used | Accuracy | Agreement with the native `.pt` |
|---|---|---|
| `cv2.INTER_LINEAR` | 75.0% | 84.2% |
| `cv2.INTER_AREA` | 75.0% | 92.1% |
| `cv2.INTER_CUBIC` | 71.1% | 76.3% |
| **PIL BILINEAR + torchvision's `int()` size math** | **82.9%** | **100%**, tensor diff 0.000000 |

That 8-point gap is **larger than the entire difference between `yolo26n-cls` and the 7×
bigger `yolo26s-cls`**. Note the size arithmetic matters too: torchvision truncates with
`int()`, and using `round()` shifts the crop a pixel on some aspect ratios and changes
predictions. Anything that reimplements preprocessing must be checked with
`evaluate.py` before it is trusted.

**4. The ONNX exports themselves are faithful.** Feeding Ultralytics' own preprocessed
tensor to both the PyTorch model and the ONNX graph gave a max logit difference of
2.5e-03 and zero argmax disagreements. So when an ONNX number looks wrong, suspect
preprocessing, not the export.

## Projecting to the Raspberry Pi 5

**These are projections. Run the scripts on the Pi and replace them with real numbers.**

Scaling factor ×4–6 per core, from vector throughput: the Pi's Cortex-A76 at 2.4 GHz has
2× 128-bit NEON FMA pipes; this laptop's P-core has 2× 256-bit AVX2 FMA at a higher
clock — roughly 4× raw, with memory bandwidth widening it further on the large model.

At `camera_node`'s configured `target_fps: 15`, one frame's budget is **66.7 ms**:

| Model | Pi 5 est. @4T | Share of one frame budget | Verdict |
|---|---|---|---|
| `yolo26n-cls` | ~6–10 ms | ~10–15% | Comfortable. Model is not the bottleneck. |
| `yolo26s-cls` | ~17–25 ms | ~25–37% | Fine. |
| `vit-base` INT8 | ~195–290 ms | 3–4 frames | Visible stall on each tile departure. |
| `vit-base` fp32 | ~440–660 ms | 7–10 frames | Stalls the capture thread for ~half a second per tile. |

`camera_node`'s existing per-frame cost projects to ~5–8 ms, i.e. a ~125–200 fps ceiling
before any model — comfortably above the 15 fps target.

**The ViT's stall is an architecture problem, not a speed problem.** `CameraWorker` runs
capture → segment → track → pipeline on one thread, so a 0.5 s inference blocks frame
capture and freezes the MJPEG stream each time a tile leaves. Deploying the ViT would
mean moving inference to its own thread/queue, not just accepting the latency. The nano
model needs no such change.

Memory is a non-issue on a 16 GB Pi: the largest artifact is 343 MB of weights. (The
`rss_delta_mb` field in the JSON reads ~1–2 MB and should be ignored — ONNX Runtime
memory-maps weights, so RSS does not move at session creation.)

## What these models actually classify — read before drawing conclusions

The four classes are `3A / 3B / 4 / 5`: **cosmetic grade tiers on intact tiles.** None of
these models detect cracks or broken corners — that is `camera_node`'s classical CV path,
and per `.claude/CLAUDE.md` it is still unvalidated for true positives because the dataset
contains essentially no damaged tiles.

Trained on 376 source photos, validated on 76. At n=76 the 95% interval is roughly ±9
points, so `yolo26n-cls` (82.9%) and `yolo26s-cls` (85.5%) are **statistically
indistinguishable** — their intervals overlap almost entirely. The ViT's lead is larger
but its interval still overlaps `yolo26s-cls`'s. Do not read these differences as
settled.

The honest summary: model size is not this project's constraint. A 6.2 MB model runs 250
frames/sec of full pipeline on a laptop and will comfortably hold line rate on the Pi.
What limits the camera station is the dataset.
