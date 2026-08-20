# development/

Dev-only tooling for calibrating `camera_node`'s vision thresholds against
real tile photos, plus a launcher for `pick_place_node`'s digital twin GUI.
Not part of any App Bricks node (see the repo root `.CLAUDE/CLAUDE.md` — this
folder is intentionally outside the `acoustic_node/`/`camera_node/`/
`pick_place_node/` convention).

| File | What it does |
|---|---|
| `tile_param_tuner.py` | Interactive GUI — drag sliders, watch the effect live on one photo at a time, save the values you land on to an XML file. |
| `analyze_dataset.py` | Batch script — runs every photo under `data/` through the real `camera_node` segmentation/crack/corner modules and prints statistically-derived recommended values. |
| `run_pick_place_twin.py` | Launcher for `pick_place_node`'s mock digital twin GUI (`--mode desktop` for a native matplotlib window, `--mode browser` for the Flask+Three.js WiFi dashboard). See below. |

Use `analyze_dataset.py` first to get a data-driven starting point, then
`tile_param_tuner.py` to sanity-check/hand-adjust it against individual
photos (e.g. ones the batch script flagged as failures).

---

## Manual calibration with `tile_param_tuner.py`

```bash
venv\Scripts\activate
python development\tile_param_tuner.py
```

1. **Open Image...** — pick a photo (defaults to `data/`). It's downscaled to
   max 1000px on the long side so the sliders stay responsive; a bad file
   (wrong extension, corrupt/undecodable) pops an error dialog instead of
   crashing.
2. This tool runs the **real** `camera_node` functions against the photo
   (`camera.segmentation.segment_tile()`, `camera.corner_detection.detect_broken_corner()`)
   rather than a separate reimplementation, in this order — color first,
   then the tile's border from that isolated region, then a verdict:
   - **1. Original** — the photo as loaded.
   - **2. Color Isolation** — `segment_tile()`'s HSV mask (the *actual*
     production tile-finding step). If no region passes the color + minimum-
     area filter, this falls back to the raw (unfiltered) HSV match so you
     can tell whether nothing matched at all vs. something matched but was
     too small.
   - **3. Tile Edge** — Canny edges of the isolated tile's crop only (not
     the whole photo — the checkerboard calibration background would drown
     out anything real). This is the same crop `crack_detection.py` runs on
     in production, so this panel doubles as a preview of what that
     detector actually sees.
   - **4. Bounding Box + Verdict** — the tile's ideal minimum-area rectangle
     drawn in **green** (what it would look like if perfectly intact)
     against its **actual** detected outline in **yellow**, whether or not
     the two agree — a corner break shows up directly as a visible gap
     between them. A text verdict reports one of:
     - `PERFECT 9x9in SQUARE` — intact, both dimensions close to 9in.
     - `NOT a perfect square - W.Win x H.Hin` — no corner flagged as broken,
       but the detected shape isn't square either (check by eye — camera
       angle/rotation can cause this too, not just damage).
     - `CORNER BROKEN - missing ~D.Din deep, A.A sq in` — flagged broken,
       with the real physical size of the missing material (see
       `TILE_SIZE_INCHES` in the script — these sample tiles are 9x9in).
3. **Sliders**, what they map to in `camera_node/python/camera/config.yaml`:

   | Slider | config.yaml key |
   |---|---|
   | Hue low / high, Sat low / high, Val low / high | `segmentation.hsv_lower` / `hsv_upper` |
   | Canny low / high | `crack_detection.canny_low` / `canny_high` |
   | Min fill ratio (%) | `corner_detection.min_fill_ratio` |
   | Max missing extent (%) | `corner_detection.max_missing_extent_fraction` — this is the one that actually catches diagonal corner chips; see `corner_detection.py`'s docstring |
   | Min tile area (% of frame) | not directly a config key — `segmentation.min_tile_area_px` is an absolute pixel count, scaled to the *live* frame size, not this tool's downscaled working image (see the scale-transfer warning below) |

4. **Save Parameters...** / **Load Parameters...** — writes/reads the
   current slider values as pretty-printed XML (default
   `development/output/tile_vision_params.xml`), tagged with the source
   photo path and a timestamp. Load a saved file later to pick up where you
   left off, or to compare two calibration sessions. (Schema note: the XML's
   `CornerDetection` element replaced an older `RectangleDetection` element
   from before this tool ran the real `corner_detection.py` — old saved
   files from before 2026-08-12 won't load.)

### What to look for while tuning HSV

Drag the Hue/Sat/Val sliders until the **Color Isolation** panel shows
*only* the tile — solid color, no background bleeding in, no holes/gaps in
the tile itself from shadow or glare. Watch a few different photos (different
lighting, different backgrounds — plain paper vs. the floor-background shots
in the `*(Cam)` folders) before settling on a range; a range that looks
perfect on one photo can fail on another with different lighting.

**Known trap**: hue is meaningless on near-gray pixels (very low saturation
or extreme value/glare). If you eyeball a wide hue range because a couple of
glare spots on the tile "need" it, you'll also start matching background
objects that happen to have unstable hue at low saturation. Prefer widening
*Sat low* or narrowing *Val high* to exclude glare, rather than widening Hue,
if you see glare-related mismatches.

---

## Batch calibration with `analyze_dataset.py`

```bash
venv\Scripts\activate
python development\analyze_dataset.py
```

Runs every photo in `data/` (currently 380) through the real
`camera.segmentation` / `camera.crack_detection` / `camera.corner_detection`
modules and prints recommended values, then saves them to
`development/output/tile_vision_params_calibrated.xml`.

### How it derives each value (and why you can trust — or shouldn't trust — it)

- **HSV range**: every photo in `data/` is a single tile centered on a
  checkerboard calibration sheet, so a small box at the exact center of each
  photo is guaranteed to be pure tile color. The script samples that box
  across all photos and takes the 0.5th/99.5th percentile per channel (with
  a small outward margin). Hue is computed only from well-lit pixels
  (`S >= 40`, `15 <= V <= 245`) — see the "known trap" above; without this
  filter the recommended hue range came out as 2-125 (nearly useless) instead
  of the actual 6-15.
- **Off-center photos are auto-excluded**: a later photo session in the
  dataset frames the tile to one side (not centered), which would otherwise
  poison the center-crop sample with background/equipment colors. These are
  detected and skipped automatically (center-crop median saturation < 80 —
  there's a clean gap in the data between "definitely on the tile" (sat
  >130) and "definitely on the background" (sat <40), see the script's
  comments) and reported as a count, not silently dropped.
- **Corner `min_fill_ratio`**: every tile in this dataset is factory-intact
  (none are damaged), so the fill-ratio (contour area / its own
  min-area-rect area) `segment_tile()` measures on each one is a real sample
  of what "healthy" looks like. The script takes the 1st percentile minus a
  small margin as the threshold — loose enough that photography noise never
  false-flags a healthy tile as broken, in this dataset.
- **Crack detection Canny thresholds + `border_margin_px`**: same idea —
  since no tile in the dataset is actually cracked, the crack detector
  "should" report clean on all of them. The script grid-searches Canny
  thresholds and found (mid-session, not before) that `detect_cracks()` was
  flagging ~98-100% of known-intact tiles as cracked, regardless of Canny
  threshold. Root cause: `detect_cracks()` runs on segmentation's tight
  bounding-box crop, so the tile's own silhouette edge against the
  background sits right at the crop's border — a long, thin, high-contrast
  line that looks exactly like what the detector is looking for. Fixed by
  adding `crack_detection.py`'s `border_margin_px` parameter, which blanks a
  border band of the Canny edge map before contour matching. The false
  positive rate dropped from ~98% (margin=0) to ~2% (margin=40) on this
  dataset. See `camera_node/python/camera/config.yaml`'s comments for the
  chosen default and why it's not simply "40" (scale-transfer caveat below).
- **Corner `max_missing_extent_fraction`**: `fill_ratio` alone turned out to
  badly under-catch diagonal/triangular corner chips — the realistic way
  ceramic actually breaks — because a triangular chip removes much less
  *area* than a square notch reaching the same distance into the tile. A
  synthetic chip whose two legs each reach halfway across a tile's edge only
  dropped `fill_ratio` to ~0.87, above the production `min_fill_ratio`
  (0.83) — it would not have been flagged. Fixed by adding a second,
  independent check: how *deep* the gap between the tile's actual shape and
  its ideal bounding rectangle reaches, measured via a distance transform
  and expressed as a fraction of the tile's own side length (scale-
  independent, unlike a raw pixel count). The script measures this on every
  known-intact photo (mean ~2.7%, max ~19%) and recommends a threshold with
  margin above that max — see `corner_detection.py`'s docstring for the full
  story, including why the more obvious "bounding box of the missing
  region" approach was tried first and rejected (it picks up harmless
  segmentation-roughness noise around the *entire* tile perimeter, not just
  a genuine chip).

### A real defect turned up during manual GUI checking

Running `tile_param_tuner.py` against a sample of real photos (not the full
batch) surfaced `data/9x9-5(Cam)/DSC_0065.JPG` — an actual hairline crack
near the tile's top-left corner, visible by eye once you know to look. This
contradicts the "every photo is factory-intact" assumption `analyze_dataset.py`
relies on for the false-positive-floor calibration above (harmless for that
purpose in this one instance, since one mistakenly-included cracked photo
barely moves a 300+-photo distribution) — but it's also the first genuine
true-positive check either detector has had: `detect_cracks()` on that photo,
with the calibrated settings, correctly reported `crack_detected=True,
length=213.8px, severity=major`, and the flagged contour visually lines up
with the real crack. Worth manually eyeballing the rest of `data/` for more
examples like this — if there are others, pulling them into a separate
"known damaged" set would let this calibration script do a real sensitivity
check instead of only a false-positive-rate one.

### What this calibration does **not** tell you

- **True-positive sensitivity is unvalidated for corner breaks; crack
  detection has exactly one real data point.** Every number above was tuned
  to *not false-flag a healthy tile* — none of it says whether a real crack
  or broken corner would actually get caught, with the single exception
  above. Re-run this script (or at least eyeball results) against more real
  damaged-tile photos as soon as any exist.
- **Absolute-pixel values don't transfer across resolution/distance.**
  `segmentation.min_tile_area_px` and `crack_detection.border_margin_px` are
  measured in pixels at whatever resolution/distance the source photos were
  taken at. This dataset's photos are close-up calibration shots (tile fills
  30-60% of a 4000-6000px-wide frame); the live `camera_node` pipeline runs
  on 640x480 frames from a camera at whatever distance it ends up mounted at
  on the real conveyor (not decided yet — see the repo root `.CLAUDE/CLAUDE.md`
  Known Technical Debt). `min_tile_area_px` was deliberately left alone by
  this script for that reason; `border_margin_px` was given a
  middle-ground default rather than the dataset's own best-scoring value.
  Re-derive both once the real camera position is fixed.
- **HSV, Canny thresholds, and `min_fill_ratio` are scale-independent** (they
  describe color and a shape ratio, not an absolute pixel count), so those
  three should transfer reasonably well to the live camera — but re-check
  them anyway once real conveyor lighting is available; this dataset's
  photos were taken under different lighting than the conveyor will use.

---

## Running the pick-and-place digital twin GUI with `run_pick_place_twin.py`

```bash
venv\Scripts\activate
python development\run_pick_place_twin.py                 # native window (default)
python development\run_pick_place_twin.py --mode browser   # WiFi dashboard
```

Thin wrapper around `pick_place_node/python/pick_place`'s two existing twin
entry points (`desktop_twin.py` / `twin_prototype.py`) — same
`MockGantrySimulator` mock motion and `pick_place/config.yaml` either way,
just so you don't have to `cd pick_place_node\python` and run the
`pick_place.*` modules directly. No real gantry hardware exists yet (see
`pick_place_node/README.md`); both modes show simulated position/vacuum-state
data only.

- `--mode desktop` (default) — matplotlib 3D window, no browser/network. Close
  the window (or Ctrl+C) to stop.
- `--mode browser` — Flask + vendored Three.js dashboard at
  `http://<this machine's IP>:5050/`, reachable from any browser on the same
  WiFi. Ctrl+C to stop.

## Data

Sample photos live in the repo root's `data/` (gitignored — see `.gitignore`,
audio/image datasets never get committed). Both tools default their file
pickers to that folder if it exists.
