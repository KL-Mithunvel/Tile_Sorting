import numpy as np

from acoustic.hardware_trigger import HardwareTapDetector

SAMPLE_RATE = 44100
BLOCK_SIZE = 512


def block(size: int = BLOCK_SIZE) -> np.ndarray:
    return np.zeros(size, dtype=np.float32)


def make_detector(**overrides) -> HardwareTapDetector:
    kwargs = dict(
        sample_rate=SAMPLE_RATE,
        block_size=BLOCK_SIZE,
        pre_trigger_ms=20,  # ~2 blocks at this block size
        capture_duration_s=0.05,  # ~5 blocks
        cooldown_s=0.05,
    )
    kwargs.update(overrides)
    return HardwareTapDetector(**kwargs)


def test_no_tap_never_captures():
    detector = make_detector()
    for _ in range(50):
        assert detector.process_block(block()) is None
    assert not detector.is_capturing


def test_notify_tap_triggers_and_eventually_returns_clip():
    detector = make_detector()
    detector.process_block(block())
    detector.process_block(block())

    detector.notify_tap()

    clip = None
    for _ in range(20):
        clip = detector.process_block(block())
        if clip is not None:
            break

    assert clip is not None
    assert clip.dtype == np.float32


def test_tap_pending_is_consumed_only_once():
    detector = make_detector()
    detector.notify_tap()
    # First block after notify should start capturing.
    detector.process_block(block())
    assert detector.is_capturing

    # A fresh detector with no second notify_tap() should not double-trigger
    # once the first capture completes.
    detector2 = make_detector()
    detector2.notify_tap()
    clip = None
    for _ in range(20):
        clip = detector2.process_block(block())
        if clip is not None:
            break
    assert clip is not None
    assert not detector2.is_capturing
    # No further notify_tap() called -> should not start a second capture.
    for _ in range(10):
        assert detector2.process_block(block()) is None


def test_cooldown_blocks_immediate_retrigger():
    detector = make_detector()
    detector.notify_tap()

    first_clip = None
    for _ in range(20):
        first_clip = detector.process_block(block())
        if first_clip is not None:
            break
    assert first_clip is not None

    # A tap notified immediately after should be ignored during cooldown.
    detector.notify_tap()
    assert detector.process_block(block()) is None
    assert not detector.is_capturing
