"""ONNX Runtime inference wrapper for the tile-grade classifiers.

One class, `OnnxClassifier`, that takes the BGR crop `camera_node` already
produces (`TileRegion.cropped_bgr`) and returns a grade + confidence. Deliberately
depends on nothing heavier than numpy + opencv + onnxruntime, so it can run on
the Pi station without torch, ultralytics or transformers installed — which is
the whole point of exporting to ONNX in the first place.

Preprocessing is the part that silently ruins accuracy if you get it wrong, so
it is named per model in config.yaml rather than assumed:

  "yolo_cls" — Ultralytics classify: resize short side to `size`, center-crop
               `size`, BGR->RGB, /255. Its Normalize step is mean=0/std=1,
               i.e. a no-op (verified from `classify_transforms()`), so there
               is deliberately no mean/std shift here.
  "vit_hf"   — HF ViTImageProcessor: resize straight to (size, size) bilinear
               (no crop), BGR->RGB, /255, then (x - 0.5) / 0.5 (verified from
               the exported preprocessor_config.json).

Both use **Pillow** for the resize, not cv2 — see `preprocess()` for the
measurement showing why that is worth 8 accuracy points. Pillow is already a
repo dependency and ships on Raspberry Pi OS as python3-pil.

Not unit-tested with synthetic images the way `camera_node/python/camera/`'s
pure modules are. Its correctness is established more strongly than a
synthetic-input test could: `evaluate.py` reproduces each model's known
accuracy through this exact path (yolo26s-cls 85.5%, vit 92.1%), and the
tensors it produces were verified byte-identical (mean abs diff 0.000000) to
Ultralytics' own predictor output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image


@dataclass
class Prediction:
    label: str
    confidence: float
    probabilities: dict[str, float]


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - np.max(x))
    return e / e.sum()


def _to_pil(image_bgr: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))


def _resize_shortest_then_crop(pil: Image.Image, size: int) -> Image.Image:
    """torchvision `Resize(size:int)` followed by `CenterCrop(size)`.

    The size arithmetic is copied from torchvision's
    `_compute_resized_output_size` on purpose, including its **int()
    truncation** (not round()) — using round() here shifts the output by a
    pixel on some aspect ratios, which is enough to change predictions.
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
    """BGR uint8 HxWx3 -> float32 NCHW batch of 1, per the named recipe.

    **Pillow, not cv2.resize — this is not a style choice.** PIL's BILINEAR
    resize is antialiased on downscale; `cv2.INTER_LINEAR` is not, and these
    are large photos being downscaled hard to 224. Measured on the 76-image
    val split with yolo26n-cls (`camera_models/cam_edge/results/`):

        cv2.INTER_LINEAR   75.0%   84% agreement with the native .pt
        cv2.INTER_AREA     75.0%   92%
        cv2.INTER_CUBIC    71.1%   76%
        PIL, this function 82.9%  100% agreement, 0.000000 tensor diff

    So getting the resize filter wrong costs ~8 accuracy points — more than
    the entire difference between the nano and the 7x-larger model. If this
    ever needs to drop the Pillow dependency, re-run
    `camera_models/cam_edge/evaluate.py` and prove the replacement matches
    before trusting it.
    """
    if image_bgr is None or image_bgr.size == 0:
        raise ValueError("preprocess: empty image")

    if mode == "yolo_cls":
        # Ultralytics classify: shortest-side resize -> center crop -> /255.
        # Its Normalize is mean=0/std=1 (a no-op), verified from
        # classify_transforms(), so there is deliberately no mean/std shift.
        pil = _resize_shortest_then_crop(_to_pil(image_bgr), size)
        arr = np.asarray(pil, dtype=np.float32) / 255.0

    elif mode == "vit_hf":
        # HF ViTImageProcessor: straight resize to (size, size), no crop,
        # resample=2 (PIL BILINEAR), /255, then (x - 0.5) / 0.5. Verified
        # against the exported preprocessor_config.json.
        pil = _to_pil(image_bgr).resize((size, size), Image.BILINEAR)
        arr = np.asarray(pil, dtype=np.float32) / 255.0
        arr = (arr - 0.5) / 0.5

    else:
        raise ValueError(f"preprocess: unknown mode '{mode}' (expected 'yolo_cls' or 'vit_hf')")

    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None, ...])


class OnnxClassifier:
    """A loaded ONNX classifier, pinned to a fixed thread count.

    `threads` matters more than it looks on a 4-core Pi: the default lets
    ONNX Runtime take every core, which starves the capture thread and the
    Flask server. Pin it explicitly for anything running on the station.
    """

    def __init__(
        self,
        onnx_path: str | Path,
        classes: list[str],
        preprocess_mode: str,
        threads: int = 2,
        size: int = 224,
    ):
        self.onnx_path = Path(onnx_path)
        if not self.onnx_path.exists():
            raise FileNotFoundError(f"OnnxClassifier: no model at {self.onnx_path}")

        self.classes = list(classes)
        self.preprocess_mode = preprocess_mode
        self.size = size
        self.threads = threads

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(
            str(self.onnx_path), opts, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name

    def predict_tensor(self, tensor: np.ndarray) -> np.ndarray:
        """Raw forward pass on an already-preprocessed NCHW batch. Separated
        from predict() so benchmark.py can time compute without preprocessing
        cost folded in."""
        return self.session.run(None, {self.input_name: tensor})[0]

    def predict(self, image_bgr: np.ndarray) -> Prediction:
        tensor = preprocess(image_bgr, self.preprocess_mode, self.size)
        logits = self.predict_tensor(tensor)[0]
        probs = _softmax(np.asarray(logits, dtype=np.float64))
        idx = int(np.argmax(probs))
        return Prediction(
            label=self.classes[idx],
            confidence=float(probs[idx]),
            probabilities={c: float(p) for c, p in zip(self.classes, probs)},
        )


def load_image(path: str | Path) -> np.ndarray:
    """cv2.imread that actually complains instead of returning None."""
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"load_image: could not decode {path}")
    return img


def quantize_int8(src: str | Path, dst: str | Path) -> Optional[Path]:
    """Dynamic INT8 quantization. Returns the output path, or None if
    onnxruntime's quantization tools aren't installed.

    Dynamic (not static) because static quantization needs a calibration pass
    over representative data, and dynamic is the honest first measurement —
    if dynamic doesn't help, static's extra complexity needs justifying.
    """
    try:
        from onnxruntime.quantization import QuantType, quantize_dynamic
    except ImportError:
        return None

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    quantize_dynamic(str(src), str(dst), weight_type=QuantType.QInt8)
    return dst
