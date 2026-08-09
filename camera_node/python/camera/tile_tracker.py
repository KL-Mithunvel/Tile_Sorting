"""Pure tile-crossing counter: debounced presence/absence state machine.

No camera I/O — feed it one frame's tile-presence boolean at a time (e.g.
`segment_tile(...) is not None`), it hands back True on the exact frame a
tile is confirmed to have fully crossed. Debounces on both edges so a single
noisy frame (a momentary false segmentation hit, or one dropped frame while a
tile is still present) doesn't miscount. Mirrors the debounce/cooldown
pattern in acoustic_node's TriggerDetector, applied to boolean presence
instead of an RMS threshold.

This is a local, camera-frame-based count for this station's own dashboard
(project_charter.md §6.1 "Role as First Station" — the camera station owns
the running tile count). It is not the conveyor's authoritative encoder-based
tracking (Automation_Architecture.md §8) — that's a separate mechanism for
knowing which physical tile a result belongs to across stations.
"""

from __future__ import annotations


class TileTracker:
    def __init__(self, min_present_frames: int, min_absent_frames: int):
        self._min_present_frames = max(1, min_present_frames)
        self._min_absent_frames = max(1, min_absent_frames)

        self._present_streak = 0
        self._absent_streak = 0
        self._confirmed_present = False
        self._tile_count = 0

    @property
    def tile_count(self) -> int:
        return self._tile_count

    @property
    def confirmed_present(self) -> bool:
        return self._confirmed_present

    def process_frame(self, tile_detected: bool) -> bool:
        """Feed one frame's presence reading. Returns True exactly once per
        tile, on the frame where a confirmed-present tile is confirmed to
        have departed (i.e. it fully crossed)."""
        if tile_detected:
            self._present_streak += 1
            self._absent_streak = 0
        else:
            self._absent_streak += 1
            self._present_streak = 0

        if not self._confirmed_present and self._present_streak >= self._min_present_frames:
            self._confirmed_present = True

        if self._confirmed_present and self._absent_streak >= self._min_absent_frames:
            self._confirmed_present = False
            self._tile_count += 1
            return True

        return False
