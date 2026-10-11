"""Stand-ins for the camera, the striker and the UNO Q, for bring-up of the
Pi's acoustic/decision station against a REAL MQTT broker.

Run it from the repo root on any machine that can reach the broker:

    python development/fake_station_peers.py --host 192.168.1.11
    python development/fake_station_peers.py --host 192.168.1.11 --no-tap   # real mic taps
    python development/fake_station_peers.py --host 192.168.1.11 --no-ack   # watch the no-ack alarm

It publishes a camera heartbeat and a graded tile every few seconds, optionally
publishes `station/tap` (what the Nano's serial bridge will do), and answers
every `tile/<id>/final` with an ack (what the UNO Q must do). Everything it
sends is synthetic and its tile ids start with DEMO-, so it cannot be
mistaken for a real run in the station's log.

Needs paho-mqtt (`pip install paho-mqtt`).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "acoustic_node" / "python"))

from acoustic.demo import FakeCameraAndStriker  # noqa: E402
from fusion.transport import MqttBus  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--interval", type=float, default=4.0, help="seconds between fake tiles")
    p.add_argument("--transit", type=float, default=2.0, help="seconds from camera to fake tap")
    p.add_argument("--no-tap", action="store_true", help="do not publish station/tap (a real strike will)")
    p.add_argument("--no-ack", action="store_true", help="do not acknowledge finals (tests the no-ack alarm)")
    args = p.parse_args()

    bus = MqttBus(args.host, args.port, client_id="fake-station-peers")
    bus.start()
    peers = FakeCameraAndStriker(bus, args.interval, args.transit, ack=not args.no_ack, tap=not args.no_tap)
    peers.start()
    print(f"Fake camera{'' if args.no_tap else ' + striker'}{'' if args.no_ack else ' + UNO Q ack'} -> "
          f"{args.host}:{args.port}. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        peers.stop()
        bus.stop()


if __name__ == "__main__":
    main()
