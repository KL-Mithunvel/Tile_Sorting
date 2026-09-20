"""Synthetic-input tests for camera/grading_model.py.

Covers the parts that are pure logic — the preprocessing recipes and the
"is a model available?" config handling — the same way the other camera
modules are tested (.claude/CLAUDE.md Development Rule 1). The ONNX session
itself is hardware/runtime-facing and is not exercised here.

**These tests deliberately do not require onnxruntime.** The whole point of
`grading_model.py` is that a station without it still runs, so a test suite
that could only pass with it installed would be testing the wrong contract.

The preprocessing correctness bar is set elsewhere and is higher than a
synthetic-input test can reach: `camera_models/cam_edge/evaluate.py`
reproduces the model's measured 82.9% through this exact path. What these
tests protect is the *recipe* — shape, scaling, crop arithmetic, and the
mode-specific mean/std shift — so that an edit to one of the two deliberately
duplicated copies fails loudly instead of quietly costing accuracy.
"""

import json

import numpy as np
import pytest

from camera.grading_model import (
    TileGradeModel,
    _resize_shortest_then_crop,
    _softmax,
    preprocess,
)


def solid_bgr(h: int, w: int, color=(40, 80, 160)) -> np.ndarray:
    """A plain BGR image — the channel values differ so channel-order bugs
    show up as a wrong number rather than a coincidentally-equal one."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = color
    return img


class TestSoftmax:
    def test_sums_to_one(self):
        assert _softmax(np.array([1.0, 2.0, 3.0, 4.0])).sum() == pytest.approx(1.0)

    def test_preserves_argmax(self):
        assert int(np.argmax(_softmax(np.array([0.1, 5.0, 0.2, -3.0])))) == 1

    def test_is_stable_on_large_logits(self):
        # The max-subtraction exists to stop exp() overflowing to inf/nan.
        out = _softmax(np.array([1000.0, 1001.0, 999.0]))
        assert np.all(np.isfinite(out))
        assert out.sum() == pytest.approx(1.0)


class TestResizeShortestThenCrop:
    @pytest.mark.parametrize(
        "size_wh", [(300, 200), (200, 300), (224, 224), (1000, 250)]
    )
    def test_always_returns_a_square_of_the_requested_size(self, size_wh):
        from PIL import Image

        pil = Image.fromarray(solid_bgr(size_wh[1], size_wh[0]))
        assert _resize_shortest_then_crop(pil, 224).size == (224, 224)

    def test_uses_int_truncation_not_rounding(self):
        """torchvision's `_compute_resized_output_size` truncates, and a
        pixel of difference here changes predictions — see the docstring in
        grading_model.py. 224 * 333/100 = 745.92: truncation gives 745."""
        from PIL import Image

        # 100 wide x 333 tall -> short side 100 scales to 224, long side to 745.
        pil = Image.fromarray(solid_bgr(333, 100))
        w, h = pil.size
        short, long_ = (w, h) if w <= h else (h, w)
        assert int(224 * long_ / short) == 745  # not 746
        assert _resize_shortest_then_crop(pil, 224).size == (224, 224)


class TestPreprocessCommon:
    @pytest.mark.parametrize("mode", ["yolo_cls", "vit_hf"])
    def test_returns_nchw_float32_batch_of_one(self, mode):
        out = preprocess(solid_bgr(480, 640), mode, size=224)
        assert out.shape == (1, 3, 224, 224)
        assert out.dtype == np.float32

    @pytest.mark.parametrize("mode", ["yolo_cls", "vit_hf"])
    def test_output_is_contiguous(self, mode):
        # ONNX Runtime wants a contiguous buffer; the transpose above would
        # otherwise hand it a view with awkward strides.
        assert preprocess(solid_bgr(300, 300), mode).flags["C_CONTIGUOUS"]

    @pytest.mark.parametrize("mode", ["yolo_cls", "vit_hf"])
    def test_rejects_empty_image(self, mode):
        with pytest.raises(ValueError, match="empty image"):
            preprocess(np.zeros((0, 0, 3), dtype=np.uint8), mode)

    @pytest.mark.parametrize("mode", ["yolo_cls", "vit_hf"])
    def test_rejects_none(self, mode):
        with pytest.raises(ValueError, match="empty image"):
            preprocess(None, mode)

    def test_rejects_unknown_mode(self):
        with pytest.raises(ValueError, match="unknown mode"):
            preprocess(solid_bgr(224, 224), "resnet_torch")

    @pytest.mark.parametrize("mode", ["yolo_cls", "vit_hf"])
    def test_converts_bgr_to_rgb(self, mode):
        """The pipeline hands over OpenCV BGR crops but the models were
        trained on RGB. Getting this backwards does not raise — it just
        quietly grades the wrong colours — so it is pinned here."""
        # Pure blue in BGR: B=255, G=0, R=0.
        out = preprocess(solid_bgr(224, 224, color=(255, 0, 0)), mode)
        r, g, b = out[0, 0].mean(), out[0, 1].mean(), out[0, 2].mean()
        # Channel 0 of the output must be RED (low), channel 2 BLUE (high).
        assert b > r
        assert g == pytest.approx(r, abs=1e-6)


class TestPreprocessYoloCls:
    def test_scales_to_zero_one_with_no_mean_shift(self):
        """Ultralytics' Normalize is mean=0/std=1, so white must land on 1.0
        and black on 0.0. A mean/std shift creeping in here would show up as
        white going to 1.0 -> 0.5 or similar."""
        white = preprocess(solid_bgr(224, 224, (255, 255, 255)), "yolo_cls")
        black = preprocess(solid_bgr(224, 224, (0, 0, 0)), "yolo_cls")
        assert white.max() == pytest.approx(1.0)
        assert white.min() == pytest.approx(1.0)
        assert black.max() == pytest.approx(0.0)

    def test_stays_in_zero_one_for_a_mid_tone(self):
        out = preprocess(solid_bgr(400, 700, (128, 128, 128)), "yolo_cls")
        assert 0.0 <= out.min() and out.max() <= 1.0
        assert out.mean() == pytest.approx(128 / 255.0, abs=1e-3)

    def test_centre_crops_a_wide_image(self):
        """A wide image is cropped, not squashed: a centred marker survives
        and the edges are discarded. Squashing instead would change the tile's
        aspect ratio and with it the model's answer."""
        img = solid_bgr(224, 896, (0, 0, 0))
        img[:, 430:466] = (255, 255, 255)  # white band through the middle
        out = preprocess(img, "yolo_cls")
        assert out.max() == pytest.approx(1.0)  # the band survived the crop


class TestPreprocessVitHf:
    def test_applies_the_half_mean_half_std_shift(self):
        """HF ViTImageProcessor does (x/255 - 0.5) / 0.5, so the range is
        [-1, 1], not [0, 1]. Mixing the two recipes up is silent."""
        white = preprocess(solid_bgr(224, 224, (255, 255, 255)), "vit_hf")
        black = preprocess(solid_bgr(224, 224, (0, 0, 0)), "vit_hf")
        assert white.min() == pytest.approx(1.0)
        assert black.max() == pytest.approx(-1.0)

    def test_mid_grey_lands_near_zero(self):
        out = preprocess(solid_bgr(224, 224, (128, 128, 128)), "vit_hf")
        assert out.mean() == pytest.approx(0.0039, abs=1e-3)

    def test_does_not_crop_a_wide_image(self):
        """Unlike yolo_cls, the ViT recipe resizes straight to (size, size).
        An off-centre marker must therefore survive, where the cropping recipe
        would throw it away."""
        img = solid_bgr(224, 896, (0, 0, 0))
        img[:, 0:40] = (255, 255, 255)  # marker at the far LEFT edge
        out = preprocess(img, "vit_hf")
        assert out.max() > 0.9  # squashed in, not cropped out


class TestFromConfig:
    """`from_config` must return None rather than raise for every "not set
    up" case — grading is optional and a station without it still inspects
    tiles. Only a model that IS present but misconfigured is worth stopping
    for."""

    def test_returns_none_when_disabled(self):
        assert TileGradeModel.from_config({"grading_model": {"enabled": False}}) is None

    def test_returns_none_when_the_block_is_absent_entirely(self):
        # An older config.yaml predating the feature must still boot.
        assert TileGradeModel.from_config({}) is None

    def test_returns_none_when_the_block_is_null(self):
        # `grading_model:` with nothing under it parses to None, not {}.
        assert TileGradeModel.from_config({"grading_model": None}) is None

    def test_returns_none_when_the_model_file_is_missing(self, capsys):
        result = TileGradeModel.from_config(
            {"grading_model": {"enabled": True, "path": "models/not_here.onnx"}}
        )
        assert result is None
        assert "no model file" in capsys.readouterr().out

    def test_returns_none_when_no_classes_are_known(self, tmp_path, capsys):
        """A model with no sidecar JSON and no configured class list cannot
        have its outputs named, so it is unusable rather than half-usable."""
        onnx = tmp_path / "nameless.onnx"
        onnx.write_bytes(b"not really onnx")
        result = TileGradeModel.from_config(
            {"grading_model": {"enabled": True, "path": str(onnx)}}
        )
        assert result is None
        assert "no class list" in capsys.readouterr().out


class TestMetadataSidecar:
    """Classes and the preprocessing recipe travel with the model in a
    sidecar JSON so they cannot drift from config.yaml — see stage_model.py."""

    def test_reads_classes_and_preprocess_from_the_sidecar(self, tmp_path):
        onnx = tmp_path / "m.onnx"
        onnx.write_bytes(b"stub")
        onnx.with_suffix(".json").write_text(
            json.dumps({"classes": ["3A", "3B"], "preprocess": "vit_hf", "input_size": 384}),
            encoding="utf-8",
        )
        meta = TileGradeModel._load_metadata(onnx)
        assert meta["classes"] == ["3A", "3B"]
        assert meta["preprocess"] == "vit_hf"
        assert meta["input_size"] == 384

    def test_missing_sidecar_is_an_empty_dict_not_an_error(self, tmp_path):
        onnx = tmp_path / "m.onnx"
        onnx.write_bytes(b"stub")
        assert TileGradeModel._load_metadata(onnx) == {}

    def test_unreadable_sidecar_is_ignored_with_a_warning(self, tmp_path, capsys):
        onnx = tmp_path / "m.onnx"
        onnx.write_bytes(b"stub")
        onnx.with_suffix(".json").write_text("{ this is not json", encoding="utf-8")
        assert TileGradeModel._load_metadata(onnx) == {}
        assert "unreadable metadata" in capsys.readouterr().out


class TestResolvePath:
    def test_empty_path_is_none(self):
        assert TileGradeModel._resolve_path("") is None

    def test_absolute_path_is_taken_as_is(self, tmp_path):
        p = tmp_path / "somewhere.onnx"
        assert TileGradeModel._resolve_path(str(p)) == p

    def test_relative_path_resolves_against_the_node_root(self):
        """`path` is relative to camera_node/ so it resolves identically
        whether systemd launched from camera_node/python or a shell from the
        repo root. The staged model is committed, so this is a real lookup."""
        resolved = TileGradeModel._resolve_path("models/tile_grade_yolo26n_cls.onnx")
        assert resolved is not None
        assert resolved.exists()
        assert resolved.parent.name == "models"
