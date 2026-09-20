"""Per-tile orchestration: wires segmentation + crack_detection +
corner_detection together into one result per tile. Pure glue over the pure
modules above — no camera I/O.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from camera.corner_detection import CornerResult, detect_broken_corner
from camera.crack_detection import CrackResult, detect_cracks
from camera.segmentation import TileRegion


@dataclass
class TileRecord:
    seq: int
    timestamp: float
    crack: CrackResult
    corner: CornerResult
    grade: str
    tile_bgr: np.ndarray = field(repr=False)
    # Set after construction, once camera/snapshot.py has persisted tile_bgr
    # to disk (see worker.py / process_video.py) — None if snapshot saving is
    # disabled or hasn't happened yet.
    snapshot_path: Optional[str] = None

    # Where the tile's bounding-box centre was, in source-frame pixels, on the
    # frame this record was captured from. With the line-crossing trigger this
    # is the position at the moment of crossing, which is what makes the
    # capture point comparable between tiles.
    center: Optional[tuple[float, float]] = None
    # "line_crossing" or "departure" — which rule fired this record, so a
    # report can be read without knowing what config.yaml said at the time.
    trigger: Optional[str] = None

    # Optional ONNX classifier output (camera/grading_model.py). None when the
    # model is disabled or unavailable. Deliberately SEPARATE from `grade`:
    # `grade` is the station's rule-based decision from measured crack/corner
    # geometry, while this is a cosmetic grade-tier guess that cannot see
    # cracks or broken corners at all. Recording both lets them be compared on
    # real tiles instead of one silently overriding the other.
    model_grade: Optional[str] = None
    model_confidence: Optional[float] = None
    model_probabilities: Optional[dict] = None
    model_inference_ms: Optional[float] = None


def grade_tile(crack: CrackResult, corner: CornerResult) -> str:
    """First-pass rule-based grading — see project_charter.md §10.1 for the
    same style of rule used for the full multi-station system. This is the
    camera station's own local grade, not the master's fused final grade."""
    if corner.corner_broken or crack.severity == "major":
        return "Reject"
    if crack.severity == "minor":
        return "Grade B"
    return "Grade A"


def process_tile(
    seq: int,
    region: TileRegion,
    canny_low: int,
    canny_high: int,
    min_crack_length_px: float,
    min_aspect_ratio: float,
    minor_severity_max_length_px: float,
    blur_kernel_size: int,
    min_fill_ratio: float,
    border_margin_px: int = 0,
    max_missing_extent_fraction: float = 1.0,
    tile_size_inches: Optional[float] = None,
    grading_model: Optional[object] = None,
    trigger: Optional[str] = None,
) -> TileRecord:
    """Run the detectors on one tile region and build its record.

    `grading_model` is anything with a `.predict(tile_bgr)` returning an
    object with label/confidence/probabilities/inference_ms — normally a
    `camera.grading_model.TileGradeModel`, typed loosely here so this module
    stays pure glue and does not import the ONNX-backed one (which would drag
    onnxruntime into every import of the pipeline). None skips model grading.

    A model failure is caught and recorded as no prediction rather than
    propagated: a classifier that cannot load an image must not stop the
    station from reporting the crack/corner result it already computed.
    """
    crack = detect_cracks(
        region.cropped_bgr,
        canny_low=canny_low,
        canny_high=canny_high,
        min_crack_length_px=min_crack_length_px,
        min_aspect_ratio=min_aspect_ratio,
        minor_severity_max_length_px=minor_severity_max_length_px,
        blur_kernel_size=blur_kernel_size,
        border_margin_px=border_margin_px,
    )
    corner = detect_broken_corner(
        region.contour,
        min_fill_ratio=min_fill_ratio,
        max_missing_extent_fraction=max_missing_extent_fraction,
        tile_size_inches=tile_size_inches,
    )
    grade = grade_tile(crack, corner)

    record = TileRecord(
        seq=seq,
        timestamp=time.time(),
        crack=crack,
        corner=corner,
        grade=grade,
        tile_bgr=region.cropped_bgr,
        center=region.center,
        trigger=trigger,
    )

    if grading_model is not None:
        try:
            prediction = grading_model.predict(region.cropped_bgr)
        except Exception as exc:  # noqa: BLE001 - see docstring
            print(f"[grading_model] prediction failed for tile {seq}: {exc}")
        else:
            record.model_grade = prediction.label
            record.model_confidence = prediction.confidence
            record.model_probabilities = prediction.probabilities
            record.model_inference_ms = prediction.inference_ms

    return record


def tile_record_to_dict(record: TileRecord) -> dict:
    """JSON/API-friendly view of a TileRecord — everything except the raw
    tile_bgr pixel array. Shared by the live dashboard (dashboard.py) and the
    offline video-file tool (process_video.py) so both report tiles the same
    way."""
    return {
        "seq": record.seq,
        "timestamp": record.timestamp,
        "grade": record.grade,
        "crack_detected": record.crack.crack_detected,
        "crack_length_px": round(record.crack.crack_length_px, 1),
        "crack_severity": record.crack.severity,
        "corner_broken": record.corner.corner_broken,
        "corner_fill_ratio": round(record.corner.fill_ratio, 3),
        "corner_missing_area_px": round(record.corner.missing_area_px, 1),
        "corner_missing_depth_px": round(record.corner.missing_depth_px, 1),
        "corner_missing_extent_fraction": round(record.corner.missing_extent_fraction, 3),
        "corner_missing_area_sq_inches": (
            round(record.corner.missing_area_sq_inches, 2)
            if record.corner.missing_area_sq_inches is not None
            else None
        ),
        "corner_missing_depth_inches": (
            round(record.corner.missing_depth_inches, 2) if record.corner.missing_depth_inches is not None else None
        ),
        "snapshot_path": record.snapshot_path,
        "center_x": round(record.center[0], 1) if record.center else None,
        "center_y": round(record.center[1], 1) if record.center else None,
        "trigger": record.trigger,
        "model_grade": record.model_grade,
        "model_confidence": (
            round(record.model_confidence, 4) if record.model_confidence is not None else None
        ),
        "model_probabilities": (
            {k: round(v, 4) for k, v in record.model_probabilities.items()}
            if record.model_probabilities
            else None
        ),
        "model_inference_ms": (
            round(record.model_inference_ms, 2) if record.model_inference_ms is not None else None
        ),
    }
