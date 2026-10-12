# master_node — optional observer / data collector / commander

The tile line **does not need this**. The Raspberry Pi decides each tile's final
grade and sends it to the UNO Q itself (`documents/programming/station_coordination.md`).
This program connects to the Pi's MQTT broker to:

- **inspect** — station health, every tile's camera / sound / final result, alarms;
- **collect** — its own SQLite copy (`data/master/master.db`) and a CSV export of every tile;
- **control** — sound vote on/off, hold-all, belt speed, strike inhibit (off by default).

```bash
cd master_node/python
python -m master.live_master                  # broker 192.168.1.11 (the Pi)
python -m master.live_master --host localhost # a broker on this machine
python -m master.live_master --controls       # enable the command buttons
```

Open <http://localhost:8000/>. Config: `master_node/python/master/config.yaml`.

## Plant 3D view (added 2026-10-12)

`/` is a Three.js view of the whole line: belt, camera / acoustic / pick-and-place
stations, the Pi rack, four bin stacks and a hold tray. Status lights follow the Pi's health,
tiles ride the belt coloured by grade, click a station (scene or list) for its detail,
W A S D / Q E fly, the buttons jump to a station. The right panel is the line control
(start/stop belt, hold-all, sound vote, striker, clear alarms) - the same validated
endpoints as before, still off unless `--controls`. The previous page is at `/classic`.

```bash
python -m master.live_master --demo    # synthetic line, no broker, controls on, in-memory DB
```

`--demo` runs `master/demo.py`, a pretend line that speaks the station's message
contract (including a camera fault every ~50 s) and obeys the commands. Its data never
touches `data/master/master.db`. Everything it shows is synthetic.

**Honest limits.** Tile positions are *derived* (age along the belt, held at the next
station until it has reported), not measured. "Start/Stop belt" only sends a belt
percent to the Nano; there is no whole-line start/stop message in the contract yet.
Troubleshoot and AI insights tabs are placeholders for the next steps.
Checked in headless Edge at 1500 px only; the page JS has no automated tests, and
clicking a station / the camera fly-to were not exercised.

Status: built and unit-tested on the dev laptop (34 tests), page rendered against seeded
data; **never connected to a real broker**. The page is unauthenticated; the commands
change how the line behaves, so they stay disabled unless you ask for them, and the
network is assumed isolated (`documents/electrical/network_plan.md`).

## Analysis view (added 2026-10-12)

`/analysis` (`GET /api/analysis?window=<seconds>`, window one of 300, 900, 3600,
21600, 86400): tiles over time by final grade (with a table view), grade mix,
destinations, throughput, reject / hold / timed-out rates, how often the camera and sound
grades match (a camera x sound table), camera-to-final-grade time and alarms by kind.
All numbers come from the master's own SQLite record (`master/analysis.py`, pure, tested).

- Rates over fewer than 20 graded tiles are shown with a "treat as noise" note.
- Agreement compares the two *sensors*; sound only changes the final grade when its vote is on.
- The camera-to-final time uses the stations' own timestamps; its 90th percentile is the
  number to use when choosing `ledger.max_wait_s` (30 s is a placeholder).
- In `--demo` the sound grade is copied from the camera grade, so 100 % agreement there
  means nothing. Checked in headless Edge at 1300 px only.
