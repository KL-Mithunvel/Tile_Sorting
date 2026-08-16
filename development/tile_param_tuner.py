"""Standalone GUI tool for tuning camera_node vision parameters against real
tile photos, before those values get copied into camera_node/python/camera/config.yaml.

Not part of any App Bricks node — this is a dev-only sandbox (per
.CLAUDE/CLAUDE.md's "development/" is outside the acoustic_node/camera_node/
pick_place_node convention).

Runs the REAL camera_node production functions (camera.segmentation.segment_tile,
camera.corner_detection.detect_broken_corner) against the loaded photo, rather
than a local reimplementation. That used to be a deliberate duplication (see
git history) so this tool kept working even if that package's API changed —
but corner_detection.py's actual logic is no longer simple enough to safely
duplicate a second time (it already had two real bugs found and fixed against
this same photo set, see its docstring); importing the real thing is safer
and is also the whole point now: seeing what production actually does.

Usage:
    venv\\Scripts\\activate
    python development\\tile_param_tuner.py

Pick an image via the "Open Image..." button (defaults to the repo's data/
folder). Drag the sliders to see live, in this order:
  1. Color Isolation — segment_tile()'s HSV mask, same as production.
  2. Tile Edge — Canny on the isolated tile's crop (not the whole photo, so
     the checkerboard calibration background doesn't drown it out).
  3. Bounding Box + Verdict — the tile's ideal minimum-area rectangle (green)
     drawn against its actual detected outline (yellow), whether or not
     they match, plus a text verdict: intact vs. corner-broken (and, if
     broken, how big — see TILE_SIZE_INCHES).
"Save Parameters..." writes the current slider values to an XML file.

Uses Tkinter + Pillow for display instead of cv2.imshow/cv2.createTrackbar
because this repo pins opencv-python-headless (no HighGUI support) for the
camera_node dashboard's server use case.
"""

from __future__ import annotations

import sys
import tkinter as tk
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional
from xml.dom import minidom

import cv2
import numpy as np
from PIL import Image, ImageTk

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE_DIR = REPO_ROOT / "data"
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent / "output" / "tile_vision_params.xml"

sys.path.insert(0, str(REPO_ROOT / "camera_node" / "python"))

from camera.corner_detection import detect_broken_corner  # noqa: E402
from camera.segmentation import segment_tile  # noqa: E402

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

# These sample tiles are 9x9 inches — used only to convert missing-area/depth
# measurements into real units for the verdict panel (see corner_detection.py:
# it derives pixels-per-inch from the tile's own detected size each time, so
# this needs no separate camera calibration).
TILE_SIZE_INCHES = 9.0

# Starting point copied from camera_node/python/camera/config.yaml's
# calibrated terracotta HSV/Canny/corner-detection values (see
# development/README.md and development/analyze_dataset.py — derived
# 2026-08-11 from 311 real tile photos), so this tool and the real pipeline
# agree on where to start tuning from. Re-sync if config.yaml is recalibrated.
DEFAULT_PARAMS = {
    "hsv_lower": (6, 0, 37),
    "hsv_upper": (15, 208, 225),
    "canny_low": 75,
    "canny_high": 200,
    "min_area_fraction": 0.05,  # tile must cover at least this fraction of the frame
    "min_fill_ratio": 0.83,  # contour_area / min-area-rect area
    "max_missing_extent_fraction": 0.22,  # how deep a gap may reach before it's a broken corner
}

# Working images are downscaled to this before any processing so the sliders
# stay responsive on full-resolution phone photos.
MAX_WORKING_DIM = 1000


class UnsupportedImageError(Exception):
    """Raised when a selected file is not a readable image in a supported format."""


# ---------------------------------------------------------------------------
# Pure image-processing helpers (no Tk/GUI dependency, could be unit-tested
# with synthetic images the same way camera_node's modules are).
# ---------------------------------------------------------------------------


def load_image_bgr(path: str) -> np.ndarray:
    """Load an image file as BGR, raising UnsupportedImageError on anything
    that isn't a readable image in a supported format."""
    ext = Path(path).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedImageError(
            f"Unsupported file type '{ext or '(none)'}'. Expected one of: "
            f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    image = cv2.imread(path, cv2.IMREAD_COLOR)
    if image is None:
        raise UnsupportedImageError(
            f"OpenCV could not decode '{path}' as an image (corrupt file or unreadable format)."
        )
    return image


def downscale_for_processing(bgr: np.ndarray, max_dim: int = MAX_WORKING_DIM) -> np.ndarray:
    h, w = bgr.shape[:2]
    scale = min(1.0, max_dim / float(max(h, w)))
    if scale >= 1.0:
        return bgr
    return cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def compute_edges(bgr: np.ndarray, canny_low: int, canny_high: int, blur_kernel: int = 5) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (blur_kernel, blur_kernel), 0)
    return cv2.Canny(blurred, canny_low, canny_high)


def isolate_color(
    bgr: np.ndarray, hsv_lower: tuple[int, int, int], hsv_upper: tuple[int, int, int]
) -> tuple[np.ndarray, np.ndarray]:
    """Raw cv2.inRange mask, with no area filtering or morphological cleanup
    — used only as a fallback diagnostic when segment_tile() (the real
    production path, used everywhere else in this tool) finds nothing, to
    show whether any pixels matched at all vs. none."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(hsv_lower, dtype=np.uint8), np.array(hsv_upper, dtype=np.uint8))
    isolated = cv2.bitwise_and(bgr, bgr, mask=mask)
    return mask, isolated


def draw_label(img: np.ndarray, text: str, org: tuple[int, int], color: tuple[int, int, int], scale: float = 0.55) -> None:
    """cv2.putText with a black outline so it stays legible over any
    background color."""
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def save_params_xml(path: Path, params: dict, source_image_path: str) -> None:
    root = ET.Element("TileVisionParams")

    color_el = ET.SubElement(root, "ColorIsolation")
    ET.SubElement(
        color_el,
        "HSVLower",
        h=str(params["hsv_lower"][0]),
        s=str(params["hsv_lower"][1]),
        v=str(params["hsv_lower"][2]),
    )
    ET.SubElement(
        color_el,
        "HSVUpper",
        h=str(params["hsv_upper"][0]),
        s=str(params["hsv_upper"][1]),
        v=str(params["hsv_upper"][2]),
    )

    edge_el = ET.SubElement(root, "EdgeDetection")
    ET.SubElement(edge_el, "Canny", low=str(params["canny_low"]), high=str(params["canny_high"]))
    ET.SubElement(
        edge_el,
        "CornerDetection",
        min_area_fraction=f"{params['min_area_fraction']:.4f}",
        min_fill_ratio=f"{params['min_fill_ratio']:.4f}",
        max_missing_extent_fraction=f"{params['max_missing_extent_fraction']:.4f}",
    )

    ET.SubElement(
        root,
        "SourceImage",
        path=source_image_path,
        saved_at=datetime.now().isoformat(timespec="seconds"),
    )

    pretty = minidom.parseString(ET.tostring(root, encoding="unicode")).toprettyxml(indent="  ")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pretty, encoding="utf-8")


def load_params_xml(path: Path) -> dict:
    root = ET.parse(path).getroot()
    lower_el = root.find("ColorIsolation/HSVLower")
    upper_el = root.find("ColorIsolation/HSVUpper")
    canny_el = root.find("EdgeDetection/Canny")
    corner_el = root.find("EdgeDetection/CornerDetection")
    if lower_el is None or upper_el is None or canny_el is None or corner_el is None:
        raise ValueError(f"'{path}' is missing expected TileVisionParams elements.")
    return {
        "hsv_lower": (int(lower_el.get("h")), int(lower_el.get("s")), int(lower_el.get("v"))),
        "hsv_upper": (int(upper_el.get("h")), int(upper_el.get("s")), int(upper_el.get("v"))),
        "canny_low": int(canny_el.get("low")),
        "canny_high": int(canny_el.get("high")),
        "min_area_fraction": float(corner_el.get("min_area_fraction")),
        "min_fill_ratio": float(corner_el.get("min_fill_ratio")),
        "max_missing_extent_fraction": float(corner_el.get("max_missing_extent_fraction")),
    }


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


def _to_tk_image(array: np.ndarray, max_w: int = 420, max_h: int = 340) -> ImageTk.PhotoImage:
    if array.ndim == 2:
        rgb = cv2.cvtColor(array, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(array, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb)
    pil_image.thumbnail((max_w, max_h), Image.LANCZOS)
    return ImageTk.PhotoImage(pil_image)


class TileParamTunerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Tile Vision Parameter Tuner")
        self.geometry("1180x760")

        self.image_bgr: Optional[np.ndarray] = None
        self.image_path: str = ""
        self._photo_refs: dict[str, ImageTk.PhotoImage] = {}

        self._build_vars()
        self._build_layout()
        self._set_status("Open an image to begin.")

    # -- setup ---------------------------------------------------------

    def _build_vars(self) -> None:
        p = DEFAULT_PARAMS
        self.h_lo = tk.IntVar(value=p["hsv_lower"][0])
        self.s_lo = tk.IntVar(value=p["hsv_lower"][1])
        self.v_lo = tk.IntVar(value=p["hsv_lower"][2])
        self.h_hi = tk.IntVar(value=p["hsv_upper"][0])
        self.s_hi = tk.IntVar(value=p["hsv_upper"][1])
        self.v_hi = tk.IntVar(value=p["hsv_upper"][2])
        self.canny_lo = tk.IntVar(value=p["canny_low"])
        self.canny_hi = tk.IntVar(value=p["canny_high"])
        self.min_area_pct = tk.DoubleVar(value=p["min_area_fraction"] * 100)
        self.fill_ratio_pct = tk.DoubleVar(value=p["min_fill_ratio"] * 100)
        self.extent_pct = tk.DoubleVar(value=p["max_missing_extent_fraction"] * 100)

    def _build_layout(self) -> None:
        top = ttk.Frame(self, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(top, text="Open Image...", command=self.open_image).pack(side=tk.LEFT)
        ttk.Button(top, text="Save Parameters...", command=self.save_parameters).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(top, text="Load Parameters...", command=self.load_parameters).pack(side=tk.LEFT, padx=(8, 0))
        self.path_label = ttk.Label(top, text="No image loaded", anchor="w")
        self.path_label.pack(side=tk.LEFT, padx=(16, 0), fill=tk.X, expand=True)

        body = ttk.Frame(self)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        images_frame = ttk.Frame(body, padding=8)
        images_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.panels: dict[str, ttk.Label] = {}
        panel_titles = [
            ("original", "1. Original"),
            ("color", "2. Color Isolation (run first)"),
            ("edges", "3. Tile Edge (Canny, isolated crop)"),
            ("bbox", "4. Bounding Box + Verdict"),
        ]
        for idx, (key, title) in enumerate(panel_titles):
            r, c = divmod(idx, 2)
            cell = ttk.LabelFrame(images_frame, text=title, padding=4)
            cell.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")
            images_frame.grid_rowconfigure(r, weight=1)
            images_frame.grid_columnconfigure(c, weight=1)
            label = ttk.Label(cell)
            label.pack()
            self.panels[key] = label

        controls = ttk.Frame(body, padding=8, width=320)
        controls.pack(side=tk.RIGHT, fill=tk.Y)
        controls.pack_propagate(False)

        ttk.Label(controls, text="HSV color isolation (terracotta)", font=("", 10, "bold")).pack(
            anchor="w", pady=(0, 4)
        )
        self._add_slider(controls, "Hue low", self.h_lo, 0, 179)
        self._add_slider(controls, "Hue high", self.h_hi, 0, 179)
        self._add_slider(controls, "Sat low", self.s_lo, 0, 255)
        self._add_slider(controls, "Sat high", self.s_hi, 0, 255)
        self._add_slider(controls, "Val low", self.v_lo, 0, 255)
        self._add_slider(controls, "Val high", self.v_hi, 0, 255)

        ttk.Separator(controls).pack(fill=tk.X, pady=8)
        ttk.Label(controls, text="Tile edge (Canny, on isolated crop)", font=("", 10, "bold")).pack(
            anchor="w", pady=(0, 4)
        )
        self._add_slider(controls, "Canny low", self.canny_lo, 0, 500)
        self._add_slider(controls, "Canny high", self.canny_hi, 0, 500)

        ttk.Separator(controls).pack(fill=tk.X, pady=8)
        ttk.Label(controls, text="Corner-break detection", font=("", 10, "bold")).pack(anchor="w", pady=(0, 4))
        self._add_slider(controls, "Min tile area (% of frame)", self.min_area_pct, 1, 90, is_double=True)
        self._add_slider(controls, "Min fill ratio (%)", self.fill_ratio_pct, 10, 100, is_double=True)
        self._add_slider(controls, "Max missing extent (%)", self.extent_pct, 1, 60, is_double=True)

        self.status_label = ttk.Label(self, text="", padding=6, anchor="w")
        self.status_label.pack(side=tk.BOTTOM, fill=tk.X)

    def _add_slider(
        self,
        parent: ttk.Frame,
        label: str,
        var: tk.Variable,
        lo: float,
        hi: float,
        is_double: bool = False,
    ) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=label).pack(anchor="w")
        resolution = 0.5 if is_double else 1
        scale = tk.Scale(
            row,
            from_=lo,
            to=hi,
            orient=tk.HORIZONTAL,
            variable=var,
            resolution=resolution,
            command=lambda _evt: self._on_param_change(),
            length=280,
        )
        scale.pack(fill=tk.X)

    # -- actions ---------------------------------------------------------

    def open_image(self) -> None:
        initial_dir = str(DEFAULT_IMAGE_DIR) if DEFAULT_IMAGE_DIR.is_dir() else str(REPO_ROOT)
        path = filedialog.askopenfilename(
            title="Select a tile image",
            initialdir=initial_dir,
            filetypes=[
                ("Image files", " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS))),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            full_res = load_image_bgr(path)
        except UnsupportedImageError as exc:
            messagebox.showerror("Unsupported image", str(exc))
            self._set_status(f"Error: {exc}")
            return

        self.image_bgr = downscale_for_processing(full_res)
        self.image_path = path
        self.path_label.config(text=path)
        self._set_status(f"Loaded {Path(path).name} ({full_res.shape[1]}x{full_res.shape[0]}, downscaled for display).")
        self._on_param_change()

    def save_parameters(self) -> None:
        params = self._current_params()
        path_str = filedialog.asksaveasfilename(
            title="Save tuned parameters",
            initialdir=str(DEFAULT_OUTPUT_PATH.parent),
            initialfile=DEFAULT_OUTPUT_PATH.name,
            defaultextension=".xml",
            filetypes=[("XML files", "*.xml")],
        )
        if not path_str:
            return
        save_params_xml(Path(path_str), params, self.image_path)
        self._set_status(f"Saved parameters to {path_str}")

    def load_parameters(self) -> None:
        path_str = filedialog.askopenfilename(
            title="Load tuned parameters",
            initialdir=str(DEFAULT_OUTPUT_PATH.parent) if DEFAULT_OUTPUT_PATH.parent.is_dir() else str(REPO_ROOT),
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
        )
        if not path_str:
            return
        try:
            params = load_params_xml(Path(path_str))
        except (ValueError, ET.ParseError) as exc:
            messagebox.showerror("Invalid parameter file", str(exc))
            return

        self.h_lo.set(params["hsv_lower"][0])
        self.s_lo.set(params["hsv_lower"][1])
        self.v_lo.set(params["hsv_lower"][2])
        self.h_hi.set(params["hsv_upper"][0])
        self.s_hi.set(params["hsv_upper"][1])
        self.v_hi.set(params["hsv_upper"][2])
        self.canny_lo.set(params["canny_low"])
        self.canny_hi.set(params["canny_high"])
        self.min_area_pct.set(params["min_area_fraction"] * 100)
        self.fill_ratio_pct.set(params["min_fill_ratio"] * 100)
        self.extent_pct.set(params["max_missing_extent_fraction"] * 100)
        self._set_status(f"Loaded parameters from {path_str}")
        self._on_param_change()

    def _current_params(self) -> dict:
        return {
            "hsv_lower": (self.h_lo.get(), self.s_lo.get(), self.v_lo.get()),
            "hsv_upper": (self.h_hi.get(), self.s_hi.get(), self.v_hi.get()),
            "canny_low": self.canny_lo.get(),
            "canny_high": self.canny_hi.get(),
            "min_area_fraction": self.min_area_pct.get() / 100.0,
            "min_fill_ratio": self.fill_ratio_pct.get() / 100.0,
            "max_missing_extent_fraction": self.extent_pct.get() / 100.0,
        }

    # -- rendering ---------------------------------------------------------

    def _on_param_change(self) -> None:
        if self.image_bgr is None:
            return
        try:
            self._render()
        except Exception as exc:  # keep the GUI alive on bad parameter combos
            self._set_status(f"Error while processing: {exc}")

    def _render(self) -> None:
        bgr = self.image_bgr
        params = self._current_params()
        h, w = bgr.shape[:2]

        # Step 1: run the real color model first (same function production
        # uses), exactly as requested.
        region = segment_tile(
            bgr,
            hsv_lower=params["hsv_lower"],
            hsv_upper=params["hsv_upper"],
            min_area_px=(h * w) * params["min_area_fraction"],
            morph_kernel_size=5,
        )

        self._set_panel("original", bgr)

        if region is None:
            # Nothing passed the area filter - fall back to the raw (no area
            # filter, no cleanup) HSV mask so it's visible whether anything
            # matched the color at all, or whether the color range itself
            # needs adjusting.
            _raw_mask, raw_isolated = isolate_color(bgr, params["hsv_lower"], params["hsv_upper"])
            blank = np.zeros_like(bgr)
            draw_label(blank, "NO TILE DETECTED", (10, 30), (0, 0, 255))
            draw_label(blank, "(raise Max tile area or check HSV range)", (10, 55), (0, 0, 255), scale=0.45)
            self._set_panel("color", raw_isolated)
            self._set_panel("edges", blank)
            self._set_panel("bbox", blank)
            self._set_status("No tile found - nothing passed the color + min-area filter.")
            return

        # Step 2: the tile's border, from the color-isolated region's own
        # detected edge (its contour) - not a separate raw-edge search over
        # the whole photo, which the checkerboard calibration background
        # would drown out.
        color_view = cv2.bitwise_and(bgr, bgr, mask=region.mask)
        edges = compute_edges(region.cropped_bgr, params["canny_low"], params["canny_high"])
        edges_bgr = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)

        # Step 3: same corner-break check production uses (see
        # corner_detection.py) - fill_ratio AND missing_extent_fraction,
        # since fill_ratio alone misses diagonal chips (see its docstring).
        corner = detect_broken_corner(
            region.contour,
            min_fill_ratio=params["min_fill_ratio"],
            max_missing_extent_fraction=params["max_missing_extent_fraction"],
            tile_size_inches=TILE_SIZE_INCHES,
        )

        rect = cv2.minAreaRect(region.contour)
        box_points = cv2.boxPoints(rect).astype(np.int32)
        (_, _), (rect_w, rect_h), _ = rect
        side_px = max(rect_w, rect_h)
        px_per_inch = side_px / TILE_SIZE_INCHES if side_px > 0 else 1.0
        side_a_in = rect_w / px_per_inch
        side_b_in = rect_h / px_per_inch

        bbox_view = bgr.copy()
        # Ideal square (green) - what the tile WOULD look like if intact.
        cv2.drawContours(bbox_view, [box_points], 0, (0, 255, 0), 2)
        # Actual detected edge (yellow) - what's really there. When these
        # two disagree at a corner, that gap is the break.
        cv2.drawContours(bbox_view, [region.contour], -1, (0, 255, 255), 2)

        if corner.corner_broken:
            verdict = (
                f"CORNER BROKEN - missing ~{corner.missing_depth_inches:.1f}in deep, "
                f"{corner.missing_area_sq_inches:.1f} sq in"
            )
            verdict_color = (0, 0, 255)
        elif abs(side_a_in - side_b_in) > 0.3:
            verdict = f"NOT a perfect square - {side_a_in:.1f}in x {side_b_in:.1f}in"
            verdict_color = (0, 165, 255)
        else:
            verdict = f"PERFECT {TILE_SIZE_INCHES:g}x{TILE_SIZE_INCHES:g}in SQUARE"
            verdict_color = (0, 200, 0)

        draw_label(
            bbox_view,
            f"size~{side_a_in:.1f}in x {side_b_in:.1f}in  fill={corner.fill_ratio:.2f}  "
            f"extent={corner.missing_extent_fraction:.2f}",
            (10, 22),
            (255, 255, 255),
            scale=0.5,
        )
        draw_label(bbox_view, "green=ideal square  yellow=detected edge", (10, 44), (200, 200, 200), scale=0.42)
        draw_label(bbox_view, verdict, (10, h - 14), verdict_color)

        self._set_panel("color", color_view)
        self._set_panel("edges", edges_bgr)
        self._set_panel("bbox", bbox_view)
        self._set_status(
            f"{'BROKEN CORNER' if corner.corner_broken else 'intact'}  |  fill_ratio={corner.fill_ratio:.3f}  "
            f"|  missing_extent={corner.missing_extent_fraction:.3f}  |  size~{side_a_in:.1f}in x {side_b_in:.1f}in"
        )

    def _set_panel(self, key: str, array: np.ndarray) -> None:
        photo = _to_tk_image(array)
        self._photo_refs[key] = photo  # keep a reference so Tk doesn't garbage-collect it
        self.panels[key].configure(image=photo)

    def _set_status(self, text: str) -> None:
        self.status_label.config(text=text)


def main() -> None:
    app = TileParamTunerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
