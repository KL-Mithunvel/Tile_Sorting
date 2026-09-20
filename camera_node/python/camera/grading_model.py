"""Optional ONNX tile-grade classifier for the live pipeline.

Runs the model staged in `camera_node/models/` by
`camera_models/cam_edge/stage_model.py`. Hardware/runtime-facing (it loads a
file and calls into ONNX Runtime), kept apart from the pure detectors per
.claude/CLAUDE.md Development Rule 1.

**This is a second opinion, not a replacement for the classical CV path.**
The model classifies cosmetic grade tier (3A/3B/4/5) on intact tiles; it does
NOT see cracks or broken corners, which remain crack_detection.py and
corner_detection.py's job. `pipeline.py` records both and keeps the rule-based
grade as the station's decision -- see its `grade` vs `model_grade` fields.

**Optional by construction.** `onnxruntime` is imported lazily and
`config.yaml`'s `grading_model.enabled` defaults to off, so a station without
onnxruntime installed, or without the model file, runs exactly as before
rather than failing to start. `from_config()` returns None in every such case
and says why on stdout.

The preprocessing here is copied deliberately, not imported, from
`camera_models/cam_edge/runtime.py` -- camera_node must not import from
camera_models (dev-only tooling, gitignored runs/). If you change one, change
both and re-run `camera_models/cam_edge/evaluate.py` to prove the accuracy
still holds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np
from PIL import Image

# camera/grading_model.py -> camera/ -> python/ -> camera_node/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
NODE_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class GradePrediction:
    """What the model thinks this tile is."""

    label: str
    confidence: float
    probabilities: dict[str, float]
    inference_ms: float


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - np.max(x))
    return e / e.sum()


def _to_pil(image_bgr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))


def _resize_shortest_then_crop(pil: Image.Image, size: int) -> Image.Image:
    """torchvision `Resize(size:int)` + `CenterCrop(size)`.

    The size arithmetic mirrors torchvision's `_compute_resized_output_size`
    including its **int() truncation** -- round() here shifts the crop by a
    pixel on some aspect ratios and changes predictions.
    """
    w, h = pil.size
    short, long_ = (w, h) if w <= h else (h, w)
    new_short = size
    new_long = int(new_short * long_ / short)
    new_w, new_h = (new_short, new_long) if w <= h else (new_long, new_short)

    pil = pil.resize((new_w, new_h), Image.BILINEAR)
    left = int(round((new_w - size) / 2.0))
    top = int(round((new_h - size) / 2.0))
    return pil.crop((left, top, left + size, top + size))


def preprocess(image_bgr: np.ndarray, mode: str, size: int = 224) -> np.ndarray:
    """BGR uint8 HxWx3 -> float32 NCHW batch of 1.

    **Pillow, not cv2.resize -- measured, not stylistic.** PIL's BILINEAR
    antialiases on downscale and `cv2.INTER_LINEAR` does not; on the 76-image
    val split that difference cost 8 accuracy points (82.9% -> 75.0%) with no
    error raised anywhere. See camera_models/cam_edge/README.md.
    """
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("preprocess: empty image")

    if mode == "yolo_cls":
        # Ultralytics classify: shortest-side resize -> centre crop -> /255.
        # Its Normalize is mean=0/std=1, a no-op, so no mean/std shift here.
        pil = _resize_shortest_then_crop(_to_pil(image_bgr), size)
        arr = np.asarray(pil, dtype=np.float32) / 255.0

    elif mode == "vit_hf":
        # HF ViTImageProcessor: straight resize to (size, size), no crop,
        # /255, then (x - 0.5) / 0.5.
        pil = _to_pil(image_bgr).resize((size, size), Image.BILINEAR)
        arr = np.asarray(pil, dtype=np.float32) / 255.0
        arr = (arr - 0.5) / 0.5

    else:
        raise ValueError(f"preprocess: unknown mode '{mode}' (expected 'yolo_cls' or 'vit_hf')")

    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None, ...])


class TileGradeModel:
    """A loaded ONNX grade classifier.

    `threads` is pinned rather than left to ONNX Runtime's default because the
    station has four cores and three things that want them: the capture
    thread, the Flask server, and this. Letting inference take every core
    starves the MJPEG stream.
    """

    def __init__(
        self,
        onnx_path: Path,
        classes: list[str],
        preprocess_mode: str,
        threads: int = 2,
        input_size: int = 224,
    ):
        import onnxruntime as ort  # lazy: optional dependency

        self.onnx_path = Path(onnx_path)
        self.classes = list(classes)
        self.preprocess_mode = preprocess_mode
        self.input_size = input_size
        self.threads = threads

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(
            str(self.onnx_path), opts, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name

        n_out = self.session.get_outputs()[0].shape[-1]
        if isinstance(n_out, int) and n_out != len(self.classes):
            raise ValueError(
                f"TileGradeModel: {self.onnx_path.name} outputs {n_out} classes but "
                f"{len(self.classes)} class names were configured ({self.classes}). "
                f"The metadata JSON and the model disagree."
            )

    @classmethod
    def from_config(cls, config: dict) -> Optional["TileGradeModel"]:
        """Build from config.yaml's `grading_model` block, or return None.

        Returns None (never raises) for every "not set up" case -- disabled,
        missing file, onnxruntime absent -- because grading is an optional
        enhancement and a station should still inspect tiles without it. A
        model that IS present but broken does raise, since that is a
        misconfiguration worth stopping for.
        """
        cfg = config.get("grading_model") or {}
        if not cfg.get("enabled", False):
            return None

        path = cls._resolve_path(cfg.get("path", ""))
        if path is None or not path.exists():
            print(f"[grading_model] disabled: no model file at {cfg.get('path')!r}")
            return None

        meta = cls._load_metadata(path)
        classes = cfg.get("classes") or meta.get("classes")
        if not classes:
            print(f"[grading_model] disabled: no class list in config or {path.stem}.json")
            return None

        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            print("[grading_model] disabled: onnxruntime is not installed "
                  "(pip install onnxruntime)")
            return None

        model = cls(
            onnx_path=path,
            classes=classes,
            preprocess_mode=cfg.get("preprocess") or meta.get("preprocess", "yolo_cls"),
            threads=cfg.get("threads", 2),
            input_size=cfg.get("input_size") or meta.get("input_size", 224),
        )
        measured = meta.get("measured") or {}
        acc = f", measured {measured['accuracy']*100:.1f}%" if measured.get("accuracy") else ""
        print(f"[grading_model] loaded {path.name} ({len(classes)} classes, "
              f"{model.threads} threads{acc})")
        return model

    @staticmethod
    def _resolve_path(raw: str) -> Optional[Path]:
        """`path` is relative to camera_node/ (e.g. "models/foo.onnx"), so it
        resolves the same however the process was launched -- systemd from
        camera_node/python, or a shell from the repo root. Absolute paths are
        taken as-is."""
        if not raw:
            return None
        p = Path(raw)
        if p.is_absolute():
            return p
        for base in (NODE_ROOT, REPO_ROOT):
            candidate = base / p
            if candidate.exists():
                return candidate
        return NODE_ROOT / p  # for the error message

    @staticmethod
    def _load_metadata(onnx_path: Path) -> dict[str, Any]:
        """Read the sidecar JSON stage_model.py wrote, so classes and the
        preprocessing recipe travel with the model rather than being
        re-typed into config.yaml and drifting."""
        meta_path = onnx_path.with_suffix(".json")
        if not meta_path.exists():
            return {}
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[grading_model] ignoring unreadable metadata {meta_path.name}: {exc}")
            return {}

    def predict(self, tile_bgr: np.ndarray) -> GradePrediction:
        import time

        tensor = preprocess(tile_bgr, self.preprocess_mode, self.input_size)
        t0 = time.perf_counter()
        logits = self.session.run(None, {self.input_name: tensor})[0][0]
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        probs = _softmax(np.asarray(logits, dtype=np.float64))
        idx = int(np.argmax(probs))
        return GradePrediction(
            label=self.classes[idx],
            confidence=float(probs[idx]),
            probabilities={c: float(p) for c, p in zip(self.classes, probs)},
            inference_ms=elapsed_ms,
        )
