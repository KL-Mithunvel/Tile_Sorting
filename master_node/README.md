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
station until it has reported), not measured. (see "Line control" below).
All four views are built; Phase 2 adaptive grading (AI_Software_Novelty.md Idea B) is not started.
Page JS is checked by `development/drive_master_pages.py` (real headless Edge against a demo master;
not part of pytest): load errors, station select, view buttons, belt/hold-all, window switches. Not
covered: clicking a station inside the 3D canvas, hover tooltips.

Status: built and unit-tested on the dev laptop (see the handover doc for counts), page rendered against seeded
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

## Troubleshoot view (added 2026-10-12)

`/troubleshoot` (`GET /api/troubleshoot?window=<seconds>`): what needs attention now,
a per-station health timeline with uptime, recent level changes and recent alarms.

- Each finding says what the station reported, since when, what it means and a
  cheapest-first list of checks, matched on the detail strings the Pi station really
  emits. **The checks are suggestions, not verified diagnoses.** An unrecognised detail
  still produces a finding with generic advice.
- A silent Pi collapses everything else into one finding (the others read "offline" only
  as a consequence); a broken master-to-broker link reports only itself.
- History is a `health_events` table in the master's SQLite record, written only when a
  station's *level* changes. A background watcher calls `collector.tick()` every 2 s so a
  station going silent is recorded even with no page open. Time before the master first saw
  a station is "no record" and is excluded from uptime.
- The Pi's own "station reports a fault" is hidden when a specific failing station explains it.
- Recorded history only starts when this master starts; it says nothing about the line
  while the master was not running.

## AI insights view (added 2026-10-12)

> **Parked 2026-10-12:** built and tested, then deliberately set aside; to be revisited. The to-do list for coming back to it is in `TODO.md` ("PARKED ... AI insights"). The other three views do not depend on it, and with `insights.provider: none` (the default) nothing is ever sent to a model.

`/insights` (`GET /api/insights?window=`, `GET /api/insights/provider`,
`POST /api/insights/explain`). Two layers, kept apart on purpose:

1. **Rule-based anomaly detection (`insights.py`, always on, no model).** Compares the
   recent quarter of the window with the stretch before it and flags what moved by more
   than chance: reject / hold / no-tap-timeout rate (two-proportion z >= 2.5 *and* >= 10
   points), camera-vs-sound agreement, camera-to-final time (>= 1.5x and >= 1 s),
   throughput, and one alarm kind repeating (>= 5). Each carries its numbers. Too little data
   flags nothing. It shows behaviour *changed*, not why, and is relative to this master's own
   history - a line that has always been bad reads as normal.
2. **Language-model explanation (`llm.py`, only when you click Explain).** Writes a short
   plain-English reading of what layer 1 and the Troubleshoot findings reported. It never
   decides what is an anomaly and cannot change the line. Only a whitelisted summary is sent
   (titles, numbers, station names, status text - no tile ids or rows) and the page shows
   exactly what was sent. Output is labelled AI-generated; a 10 s cool-down stops loops.

```bash
python -m master.live_master --demo --ai ollama       # local model, works on the isolated LAN
ollama pull llama3.2:3b                                # once; the page says so if it is missing
set ANTHROPIC_API_KEY=...                              # then:  --ai anthropic  (needs internet)
```

`insights.provider` in `config.yaml` is `none` by default. The Claude API key is read only
from the `ANTHROPIC_API_KEY` environment variable, never from a file, and is never returned
or logged; choosing `anthropic` sends the summary to api.anthropic.com when you click.

**Not done / not trusted:** no model has been run end to end (Ollama is installed on the dev
laptop but has no model pulled; the Claude path has never been called) - the HTTP layer is
tested with a fake transport only. The thresholds are reasoned, not tuned on real line data.
The prompt has not been evaluated for faithfulness. Phase 2 adaptive grading is untouched.

## Line control: Start / Stop line and the gantry feed (added 2026-10-12)

- **Stop line** = one `station/cmd {"cmd":"line","state":"stop"}`; the Pi does it: hold-all on, belt 0, striker
  inhibited, **gantry feed paused**, remembering what it overrode. **Start line** restores the belt speed, the
  striker and hold-all, and resumes the feed; with no known belt speed it refuses rather than guess. A **soft
  stop over the network, not an e-stop** (the e-stop is wired separately).
- **Pause feed / Resume feed** (`/api/command/feed`) stops the Pi handing NEW tiles to the gantry. A tile already
  in flight completes; the gantry finishes what it has and idles with an empty cup. Held tiles are delivered in
  order on resume. If the pause outlasts the Pi's outbox (`unoq.max_outbox`) the oldest tiles are dropped: the
  gantry station warns, then fails, before that happens. While paused the final is stored on the Pi but not on
  the bus, so this master sees those tiles only after the feed resumes.
- **Gantry hard pause / resume** (`/api/command/gantry`) is different: it freezes the machine *mid-job* after the
  move in flight, so a tile may stay held on the cup. It asks for confirmation; prefer Pause feed.
- Not verified: any of this against the real UNO Q, Nano or belt (demo + unit tests only).

## Sim-backed demo: `--sim` (added 2026-10-13)

```bash
python -m master.live_master --sim      # no broker; controls on; in-memory DB
```

Where `--demo` *pretends* to be the station, `--sim` runs the real thing on the laptop: the real
`StationCoordinator` (ledger, final grade, outbox/acks, line stop/start, feed gate, health) and the
real gantry `FeedClient` -> `FinalQueue` -> `JobRunner` -> reporter -> command handler over the
protocol's `SimulatedMcu`, on an in-memory bus. Only the camera, the Nano and the microphone are
stand-ins (`master/sim_line.py`). The pill reads "sim - real station logic, simulated machine".

- It imports the station's packages (adds them to `sys.path` itself) **only when `--sim` is given**;
  nothing else in `master/` does, so a normal master still runs on any PC.
- Default: the simulated MCU moves at a fixed 400 mm/s so the demo looks alive.
  `SimLine(real_feeds=True)` honours the configured feed words instead - at today's placeholder
  config one tile then takes ~22 s, i.e. the gantry is the bottleneck of the line.
- Still simulated: no firmware, no real timing, no vacuum, no broker.

Everything done and still to do is in `documents/programming/master_gui_handover.md`.
