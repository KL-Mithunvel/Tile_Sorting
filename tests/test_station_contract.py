"""The camera node and the fusion package never import each other (separate
nodes); they only share a JSON contract. This is where the two are made to
agree: what camera/publisher.py sends, the coordinator must accept and use."""

from camera.publisher import TilePublisher
from fusion.coordinator import StationCoordinator
from fusion.store import TileStore
from fusion.transport import InMemoryBus


def test_camera_publisher_output_is_consumed_by_the_coordinator():
    bus = InMemoryBus()
    cam = TilePublisher({}, bus=bus, clock=lambda: 1000.0)
    cfg = {"ledger": {"max_wait_s": 10}, "final": {"vote_enabled": False, "bins": {"Grade B": "bin_2"}},
           "unoq": {"require_ack": False}}
    store = TileStore(":memory:")
    coord = StationCoordinator(cfg, bus, store, clock=lambda: 1000.0)
    coord.start()

    tile_id = cam.publish_tile({"seq": 1, "grade": "Grade B", "rule_grade": "Grade B"}, "camera")
    assert tile_id and coord.snapshot()["awaiting_count"] == 1
    assert coord.on_tap({"valid": True, "grade": None, "confidence": None}) == tile_id
    final = bus.messages("tile/+/final")[0]
    assert final["tile_id"] == tile_id and final["final_grade"] == "Grade B" and final["destination"] == "bin_2"
    # the camera heartbeat topic is the one the coordinator watches
    cam._bus.publish("station/camera/status", {"source_kind": "camera", "source_status": "running"}, qos=0)
    assert coord.snapshot()["health"]["camera"]["level"] == "ok"
