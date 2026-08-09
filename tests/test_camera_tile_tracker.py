from camera.tile_tracker import TileTracker


def test_counts_one_tile_pass():
    tracker = TileTracker(min_present_frames=3, min_absent_frames=3)
    events = []
    events += [tracker.process_frame(False) for _ in range(5)]
    events += [tracker.process_frame(True) for _ in range(5)]
    events += [tracker.process_frame(False) for _ in range(5)]

    assert sum(events) == 1
    assert tracker.tile_count == 1


def test_short_noise_blip_does_not_count():
    tracker = TileTracker(min_present_frames=3, min_absent_frames=3)
    tracker.process_frame(True)
    tracker.process_frame(True)  # only 2 frames — never reaches min_present_frames
    tracker.process_frame(False)
    tracker.process_frame(False)
    tracker.process_frame(False)

    assert tracker.tile_count == 0


def test_two_tiles_counted_separately():
    tracker = TileTracker(min_present_frames=2, min_absent_frames=2)
    sequence = [False, False, True, True, True, False, False, False, True, True, True, False, False, False]

    events = [tracker.process_frame(v) for v in sequence]

    assert sum(events) == 2
    assert tracker.tile_count == 2


def test_tile_stays_confirmed_present_across_a_single_dropped_frame():
    tracker = TileTracker(min_present_frames=2, min_absent_frames=3)
    tracker.process_frame(True)
    tracker.process_frame(True)  # confirmed present
    tracker.process_frame(False)  # one dropped frame, below min_absent_frames
    tracker.process_frame(True)

    assert tracker.confirmed_present
    assert tracker.tile_count == 0
