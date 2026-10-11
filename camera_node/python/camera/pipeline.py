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

    # Wall-clock cost of each stage, for the dashboard's pipeline view. The
    # model's own time is model_inference_ms above; total_ms covers everything
    # process_tile did for this tile.
    crack_ms: Optional[float] = None
    corner_ms: Optional[float] = None
    total_ms: Optional[float] = None

    # How `grade` (the station's single decision) was reached. `grade` is the
    # worse of the two below when the model took part; with no model, or one
    # too unsure to count, it equals rule_grade.
    rule_grade: Optional[str] = None  # from measured crack/corner geometry only
    model_station_grade: Optional[str] = None  # the model's tier mapped to a station grade
    model_used: bool = False  # True when the model's tier actually fed `grade`

    # Identity of the tile the line-crossing tracker followed (None in
    # departure mode, which has no ids). Not the same as `seq`: ids are
    # per-track and can skip numbers; seq counts graded tiles.
    track_id: Optional[int] = None

    # Sharpness of the frame this tile was graded on (sharpness.edge_sharpness) and
    # how many frames near the line were compared to choose it; None when the
    # sharpest-frame pick is off. Only comparable between frames of the same tile.
    sharpness: Optional[float] = None
    sharpness_candidates: Optional[int] = None


# Best -> worst. Every grade the station can output, in one vocabulary, so the
# rule-based decision (measured defects) and the model's cosmetic tier
# (3A/3B/4/5) can be compared and combined on a single scale.
GRADE_ORDER = ("Grade A", "Grade B", "Grade C", "Reject")


def worse_grade(a: str, b: str) -> str:
    """The worse of two station grades (Reject worst)."""
    return a if GRADE_ORDER.index(a) >= GRADE_ORDER.index(b) else b


def validate_tier_map(tier_map: dict, classes: list[str]) -> None:
    """Fail loudly at start-up, not on the first tile, if the config cannot
    translate every class the model can output into a station grade."""
    missing = [c for c in classes if c not in tier_map]
    if missing:
        raise ValueError(
            f"grading.tier_map has no entry for model class(es) {missing}; it maps {sorted(tier_map)}"
        )
    bad = {k: v for k, v in tier_map.items() if v not in GRADE_ORDER}
    if bad:
        raise ValueError(f"grading.tier_map values must be one of {list(GRADE_ORDER)}, got {bad}")


def fuse_grade(
    rule_grade: str,
    model_station_grade: Optional[str],
    model_confidence: Optional[float],
    min_confidence: float,
) -> tuple[str, bool]:
    """Combine the defect-based grade with the model's mapped tier.

    The two see different things: the rules see cracks and broken corners and
    are blind to cosmetic tier; the model sees cosmetic tier and is blind to
    cracks. So neither may rescue a tile the other has condemned: the result is
    the WORSE of the two. The model only gets a vote when it is sure enough
    (`min_confidence`); below that it abstains and the rules stand alone. That
    matters now: on this rig the model scores ~30% on a 4-class problem, which
    is close to chance, and letting that decide a sort would be random.

    Returns (final_grade, model_was_used).
    """
    if model_station_grade is None or model_confidence is None or model_confidence < min_confidence:
        return rule_grade, False
    return worse_grade(rule_grade, model_station_grade), True


def grading_options(config: dict, model: Optional[object]) -> dict:
    """The `tier_map` / `model_min_confidence` keyword arguments for
    `process_tile()`, from config.yaml's `grading` block, validated against the
    model's classes. Shared by the live worker and the offline video processor
    so they cannot grade the same footage differently. Returns {} (model stays
    display-only) when there is no model or no tier map."""
    grading_cfg = config.get("grading") or {}
    tier_map = grading_cfg.get("tier_map")
    if model is None or not tier_map:
        return {}
    validate_tier_map(tier_map, list(model.classes))
    return {
        "tier_map": dict(tier_map),
        "model_min_confidence": float(grading_cfg.get("model_min_confidence", 0.6)),
    }


def grade_tile(crack: CrackResult, corner: CornerResult) -> str:
    """First-pass rule-based grading — see project_charter.md §10.1 for the
    same style of rule used for the full multi-station system. This is the
    camera station's own local grade, not the master's fused final grade.
    Defects only; the model's tier is folded in afterwards by `fuse_grade`."""
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
    tier_map: Optional[dict] = None,
    model_min_confidence: float = 0.6,
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

    `tier_map` translates the model's tiers (3A/3B/4/5) into station grades.
    With it, the model's tier is combined with the defect-based grade through
    `fuse_grade`; without it (None) the model is display-only and `grade` is
    purely rule-based, exactly as before.
    """
    t_start = time.perf_counter()
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
    t_crack_done = time.perf_counter()
    corner = detect_broken_corner(
        region.contour,
        min_fill_ratio=min_fill_ratio,
        max_missing_extent_fraction=max_missing_extent_fraction,
        tile_size_inches=tile_size_inches,
    )
    t_corner_done = time.perf_counter()
    grade = grade_tile(crack, corner)

    record = TileRecord(
        seq=seq,
        timestamp=time.time(),
        crack=crack,
        corner=corner,
        grade=grade,
        rule_grade=grade,
        tile_bgr=region.cropped_bgr,
        center=region.center,
        trigger=trigger,
        crack_ms=(t_crack_done - t_start) * 1000.0,
        corner_ms=(t_corner_done - t_crack_done) * 1000.0,
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
            if tier_map is not None:
                record.model_station_grade = tier_map.get(prediction.label)
                record.grade, record.model_used = fuse_grade(
                    record.rule_grade,
                    record.model_station_grade,
                    prediction.confidence,
                    model_min_confidence,
                )

    record.total_ms = (time.perf_counter() - t_start) * 1000.0
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
        "track_id": record.track_id,
        "sharpness": round(record.sharpness, 2) if record.sharpness is not None else None,
        "sharpness_candidates": record.sharpness_candidates,
        # Size of the image the graders actually received (None when a record
        # carries no pixels, e.g. in tests).
        "crop_w": int(record.tile_bgr.shape[1]) if record.tile_bgr is not None else None,
        "crop_h": int(record.tile_bgr.shape[0]) if record.tile_bgr is not None else None,
        "rule_grade": record.rule_grade,
        "model_station_grade": record.model_station_grade,
        "model_used": record.model_used,
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
        "crack_ms": round(record.crack_ms, 2) if record.crack_ms is not None else None,
        "corner_ms": round(record.corner_ms, 2) if record.corner_ms is not None else None,
        "total_ms": round(record.total_ms, 2) if record.total_ms is not None else None,
    }
