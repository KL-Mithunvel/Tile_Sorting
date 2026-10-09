# Camera node — detection hardening build plan

Written 2026-10-09. Context: running the dashboard on the real belt clip
(`data/WhatsApp Video 2026-08-26 at 7.27.04 PM.mp4`) showed three problems that
all end in "a photo that is not a tile gets graded":

1. A brown sandal merged with the dark wood rim beside the belt segments as one
   region (68x291 px, 28% filled, aspect 0.23) and tripped the line — graded
   Reject, the only Reject on a clip of otherwise-Grade-A tiles.
2. `segment_tile()` returns only the largest region, so with two tiles in frame
   "the tile" flips on noise and its centre teleports (see CLAUDE.md, Known
   Technical Debt). `LineCrossingDetector.max_step` (2026-10-09) only *ignores*
   the jumps; it does not give tiles an identity.
3. `CameraWorker._loop()` has no error handling around the per-frame work, so
   one genuine exception would silently kill the capture thread.

The camera will be mounted directly above the belt, as in the clip, so the belt
occupies a fixed region of every frame.

Rule for every step: write the test first or alongside, run the camera suite
(`OPENBLAS_NUM_THREADS=1 python -m pytest tests/ -q -k camera`), then commit.
One commit per step. Pure logic stays I/O-free (CLAUDE.md Development Rule 1);
every threshold is a `config.yaml` value (Rule 2).

| # | Step | What changes | Proven by |
|---|---|---|---|
| 1 | Shape gate | `segmentation.py`: reject regions whose fill ratio / aspect ratio is not tile-like; pick the largest region that *passes*. New config `segmentation.min_fill_ratio`, `min_aspect`, `max_aspect`. Gate is looser than `corner_detection.min_fill_ratio` so a chipped tile is still segmented and then flagged, not silently dropped. | Synthetic: sliver/blob rejected, square and chipped square accepted. Real clip: the sandal no longer triggers. |
| 2 | Belt ROI | New pure `belt_roi.py` + `segment_*` accept a ROI; only the belt area is searched. Modes: `off`, `fixed` (normalised rect, the real rig), `color` (finds the belt lane by its own hue each frame — survives the hand-held clip). | Synthetic: object outside ROI ignored, inside found. Real clip: wood rim / sandal outside the lane never seen. |
| 3 | Worker resilience | `CameraWorker._loop()` catches per-frame exceptions, logs them, keeps running; `SharedState` records error count + last error; dashboard shows them. | Fake-capture test: a raising `process_tile` does not stop the thread, error is recorded. |
| 4 | Tile identity tracking | `segment_tiles()` returns every passing region; new pure `region_tracker.py` associates regions to persistent track ids by nearest centre; one line-crossing memory per track; worker + overlay handle several tiles. | Synthetic: two tiles with near-equal areas keep their ids, each fires exactly once. Real clip: crossing count vs the 7 real tiles. |
| 5 | Wrap-up | Re-run the clip end to end with the shipped config, update CLAUDE.md (modules, debt entries, config), tick TODO. | Full camera suite + clip numbers. |

## Known limits going in

* The clip is hand-held: the belt drifts ~100 px sideways. `fixed` ROI cannot be
  validated on it (the real rig is fixed); `color` mode can.
* The sandal and a tile can touch. A merged region fails the shape gate, which
  costs the tile for that frame — acceptable, it reappears once separated.
* Tracking assumes a tile moves less than `max_match_distance` (normalised)
  between frames. Re-derive once the real belt speed and frame rate are known.
