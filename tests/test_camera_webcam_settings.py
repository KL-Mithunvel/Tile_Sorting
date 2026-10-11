"""WebcamCapture applies the configured format and exposure, in the right order, and
reports what the driver actually gave (2026-10-11). Fake camera: no hardware."""
import sys

import cv2
import numpy as np
import pytest

from camera.capture import WebcamCapture, load_config


class FakeVideoCapture:
    """Records every set() and lets a test decide what the 'driver' accepts."""

    instances = []

    def __init__(self, index, api):
        self.index, self.api = index, api
        self.sets = []
        self.opened = True
        self.values = {cv2.CAP_PROP_FRAME_WIDTH: 640, cv2.CAP_PROP_FRAME_HEIGHT: 480, cv2.CAP_PROP_FPS: 30.0,
                       cv2.CAP_PROP_EXPOSURE: -6.0, cv2.CAP_PROP_AUTO_EXPOSURE: 0.75,
                       cv2.CAP_PROP_FOURCC: cv2.VideoWriter_fourcc(*"YUY2")}
        self.accepts = lambda prop, value: True
        FakeVideoCapture.instances.append(self)

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        self.sets.append((prop, value))
        if self.accepts(prop, value):
            self.values[prop] = value
            return True
        return False

    def get(self, prop):
        return self.values.get(prop, 0.0)

    def read(self):
        return True, np.zeros((480, 640, 3), np.uint8)

    def release(self):
        self.opened = False


@pytest.fixture(autouse=True)
def reset():
    FakeVideoCapture.instances = []


def make(**cam):
    cfg = load_config()
    cfg["camera"].update(api_preference="dshow", **cam)
    return WebcamCapture(cfg, video_capture_factory=FakeVideoCapture)


def props(cap):
    return [p for p, _ in cap._cap.sets]


class TestFormat:
    def test_the_shipped_config_asks_for_what_the_camera_can_actually_do(self):
        cam = load_config()["camera"]
        assert (cam["frame_width"], cam["frame_height"], cam["target_fps"]) == (1920, 1080, 30)
        assert cam["fourcc"] == "MJPG" and cam["buffer_size"] == 1

    def test_pixel_format_is_set_before_the_resolution(self):
        c = make()
        c.start()
        order = props(c)
        assert order.index(cv2.CAP_PROP_FOURCC) < order.index(cv2.CAP_PROP_FRAME_WIDTH) < order.index(cv2.CAP_PROP_FPS)

    def test_requested_values_are_applied(self):
        c = make(frame_width=1280, frame_height=720, target_fps=25, fourcc="MJPG", buffer_size=1)
        c.start()
        sets = dict(c._cap.sets)
        assert sets[cv2.CAP_PROP_FRAME_WIDTH] == 1280 and sets[cv2.CAP_PROP_FRAME_HEIGHT] == 720
        assert sets[cv2.CAP_PROP_FPS] == 25 and sets[cv2.CAP_PROP_BUFFERSIZE] == 1
        assert sets[cv2.CAP_PROP_FOURCC] == cv2.VideoWriter_fourcc(*"MJPG")

    def test_fourcc_and_buffer_and_gain_can_be_left_alone(self):
        c = make(fourcc=None, buffer_size=None, gain=None)
        c.start()
        assert not {cv2.CAP_PROP_FOURCC, cv2.CAP_PROP_BUFFERSIZE, cv2.CAP_PROP_GAIN} & set(props(c))

    def test_gain_is_applied_when_given(self):
        c = make(gain=40)
        c.start()
        assert dict(c._cap.sets)[cv2.CAP_PROP_GAIN] == 40

    def test_what_the_driver_really_gave_is_reported_not_what_was_asked(self):
        # this "driver" ignores the resolution and format requests, like a camera without a 1080p MJPG mode
        cfg = load_config()
        cfg["camera"].update(api_preference="dshow", frame_width=1920, frame_height=1080)
        c = WebcamCapture(cfg, video_capture_factory=lambda i, a: _stubborn())
        c.start()
        assert (c.settings["width"], c.settings["height"]) == (640, 480)
        assert c.settings["fourcc"] == "YUY2"

    def test_an_unopenable_camera_raises(self):
        cfg = load_config()
        c = WebcamCapture(cfg, video_capture_factory=lambda i, a: _closed())
        with pytest.raises(RuntimeError, match="could not open"):
            c.start()


def _stubborn():
    cap = FakeVideoCapture(0, 0)
    cap.accepts = lambda prop, value: prop not in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT, cv2.CAP_PROP_FOURCC)
    return cap


def _closed():
    cap = FakeVideoCapture(0, 0)
    cap.opened = False
    return cap


class TestExposure:
    def test_auto_leaves_the_cameras_own_exposure_alone(self):
        c = make(exposure={"mode": "auto", "value": -7})
        c.start()
        assert cv2.CAP_PROP_EXPOSURE not in props(c) and cv2.CAP_PROP_AUTO_EXPOSURE not in props(c)

    def test_manual_on_directshow_uses_directshows_codes(self):
        c = make(exposure={"mode": "manual", "value": -7})
        c.start()
        sets = dict(c._cap.sets)
        assert sets[cv2.CAP_PROP_AUTO_EXPOSURE] == 0.25 and sets[cv2.CAP_PROP_EXPOSURE] == -7
        assert c.settings["exposure"] == -7

    def test_manual_on_v4l2_uses_v4l2s_codes(self):
        cfg = load_config()
        cfg["camera"].update(api_preference="v4l2", exposure={"mode": "manual", "value": 50})
        c = WebcamCapture(cfg, video_capture_factory=FakeVideoCapture)
        c.start()
        sets = dict(c._cap.sets)
        assert sets[cv2.CAP_PROP_AUTO_EXPOSURE] == 1.0 and sets[cv2.CAP_PROP_EXPOSURE] == 50

    def test_manual_exposure_is_not_guessed_for_a_backend_not_known_to_honour_it(self):
        cfg = load_config()
        cfg["camera"].update(api_preference="msmf", exposure={"mode": "manual", "value": -7})
        c = WebcamCapture(cfg, video_capture_factory=FakeVideoCapture)
        c.start()
        assert cv2.CAP_PROP_EXPOSURE not in props(c)
        assert "not known to honour" in c.settings["note"]

    @pytest.mark.parametrize("platform,expected", [("win32", 0.25), ("linux", 1.0)])
    def test_api_any_picks_the_platforms_codes(self, monkeypatch, platform, expected):
        monkeypatch.setattr(sys, "platform", platform)
        cfg = load_config()
        cfg["camera"].update(api_preference="any", exposure={"mode": "manual", "value": 5})
        c = WebcamCapture(cfg, video_capture_factory=FakeVideoCapture)
        c.start()
        assert dict(c._cap.sets)[cv2.CAP_PROP_AUTO_EXPOSURE] == expected

    def test_bad_exposure_config_is_refused_up_front(self):
        with pytest.raises(ValueError, match="'auto' or 'manual'"):
            make(exposure={"mode": "fast"})
        with pytest.raises(ValueError, match="needs camera.exposure.value"):
            make(exposure={"mode": "manual"})
        with pytest.raises(ValueError, match="4 characters"):
            make(fourcc="MJPEG")
