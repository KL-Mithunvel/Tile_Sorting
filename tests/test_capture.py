import numpy as np

from acoustic.capture import TriggerDetector

SAMPLE_RATE = 44100
BLOCK_SIZE = 512  # samples per block


def silence(size: int = BLOCK_SIZE) -> np.ndarray:
    return np.zeros(size, dtype=np.float32)


def loud(size: int = BLOCK_SIZE) -> np.ndarray:
    return np.full(size, 0.9, dtype=np.float32)


def make_detector(**overrides) -> TriggerDetector:
    kwargs = dict(
        sample_rate=SAMPLE_RATE,
        block_size=BLOCK_SIZE,
        rms_threshold=0.5,
        pre_trigger_ms=20,  # ~2 blocks at this block size
        capture_duration_s=0.05,  # ~5 blocks
        cooldown_s=0.05,
    )
    kwargs.update(overrides)
    return TriggerDetector(**kwargs)


def test_quiet_blocks_never_trigger():
    detector = make_detector()
    for _ in range(50):
        assert detector.process_block(silence()) is None
    assert not detector.is_capturing


def test_loud_block_triggers_and_eventually_returns_clip():
    detector = make_detector()
    detector.process_block(silence())
    detector.process_block(silence())

    clip = None
    for _ in range(20):
        clip = detector.process_block(loud())
        if clip is not None:
            break

    assert clip is not None
    assert clip.dtype == np.float32


def test_clip_includes_pre_trigger_audio():
    # Distinguish pre-trigger silence from the post-trigger loud signal by value.
    detector = make_detector()
    detector.process_block(silence())
    detector.process_block(silence())

    clip = None
    for _ in range(20):
        clip = detector.process_block(loud())
        if clip is not None:
            break

    assert clip is not None
    # The clip should contain some near-zero (pre-trigger) samples, not just 0.9s.
    assert np.any(np.abs(clip) < 0.1)


def test_cooldown_blocks_immediate_retrigger():
    detector = make_detector()
    detector.process_block(silence())

    first_clip = None
    for _ in range(20):
        first_clip = detector.process_block(loud())
        if first_clip is not None:
            break
    assert first_clip is not None

    # Immediately after the first clip finishes, cooldown should suppress a
    # brand-new capture from starting even though the signal is still loud.
    assert not detector.is_capturing
    detector.process_block(loud())
    assert not detector.is_capturing
