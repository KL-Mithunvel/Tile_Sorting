# Master GUI — handover (written 2026-10-12/13; Hold-tile finding added 2026-10-13)

One page to pick the master GUI work up from: what exists, how to run it, what is and is
not verified, what we learned, and what is next. The per-feature detail lives in
`master_node/README.md`; the message contract in `station_coordination.md`.

**One-line status:** the master (`master_node/`) has four views, a line/feed/gantry control
panel and two no-hardware demo modes. Everything is laptop-only: **nothing has met a real
broker, the Pi, the UNO Q, the Nano, a belt or a camera.** AI insights is built but **parked**.

---

## 1. How to run

```bash
cd master_node/python
python -m master.live_master                  # the real thing: broker 192.168.1.11 (the Pi)
python -m master.live_master --demo           # synthetic line (master/demo.py), no broker
python -m master.live_master --sim            # REAL station + gantry logic on a simulated machine
python -m master.live_master --demo --ai ollama   # (parked) a local model explains anomalies
# then http://localhost:8000/   (/analysis /troubleshoot /insights /classic)
```

`--demo` and `--sim` use an in-memory database (never `data/master/master.db`), turn the
controls on, and show a purple pill saying which one it is. Controls are otherwise **off**
unless `--controls` or `dashboard.controls_enabled`.

Tests and the real-browser check:

```bash
OPENBLAS_NUM_THREADS=1 python -m pytest tests -q     # 877 passed, 82 skipped on 2026-10-13
python -m master.live_master --sim                   # (in master_node/python) then, from the repo root:
python development/drive_master_pages.py             # 35 checks in a real headless Edge
```

**Gotcha found today:** if port 8000 is still held by an earlier run, the new server fails to
start and the browser driver quietly tests the *old* page (it produced false failures).
Check `netstat -ano | grep :8000` before a run.

---

## 2. What is built

### Views

| URL | What it is | Key files |
|---|---|---|
| `/` | **Plant 3D** — Three.js line: belt, camera / acoustic / Pi / conveyor stations with live health lights, a four-legged Cartesian XYZ gantry (X rails, box-section Y beam on trucks, carriage, Z column + cup), four bin stacks + hold tray; orbit / WASD fly / view buttons; click a station for detail; line control panel; **the master PC (desk, tower, monitor showing a live miniature of line state / tally / mode / controls) and the operator avatar, joined to the Pi rack by a link that goes red if the master loses the broker** (both are clickable pseudo-stations in `/api/plant` only - Troubleshoot never sees them) | `templates/plant.html`, `plant.py` |
| `/analysis` | throughput, reject/hold/timeout rates, grade mix over time (chart + table), destinations, camera-vs-sound agreement, camera→final time, alarms by kind | `templates/analysis.html`, `analysis.py` |
| `/troubleshoot` | what needs attention now (meaning, since when, cheapest-first checks), per-station health timeline with uptime, recent changes and alarms | `templates/troubleshoot.html`, `troubleshoot.py`, `health_events` table |
| `/insights` | **(parked)** rule-based anomaly detection + an optional language-model explanation | `insights.py`, `llm.py`, `templates/insights.html` |
| `/classic` | the original flat overview page | `templates/master.html` |

### Control (all behind the controls guard, JSON-only, validated)

| Button | Endpoint | Sends | What it does |
|---|---|---|---|
| **Stop line / Start line** | `/api/command/line` | `station/cmd {"cmd":"line",...}` | Pi: hold-all on, belt 0, striker inhibited, **gantry feed paused**, remembering what it overrode; Start restores belt speed / striker / hold-all and resumes the feed; refuses to start at a speed it does not know. **Soft stop over the network — not an e-stop.** |
| **Pause feed / Resume feed** | `/api/command/feed` | `station/cmd {"cmd":"feed",...}` | Pi stops handing NEW tiles to the UNO Q; the gantry finishes and drains what it has, then idles with an empty cup; held tiles go out in order on resume |
| **Gantry hard pause / resume** | `/api/command/gantry` | `station/gantry/cmd` | Freezes the machine *mid-job* after the move in flight (a tile may stay on the cup). Asks for confirmation. Prefer Pause feed. |
| Set belt, Hold all, Sound vote, Striker, Clear alarms | `/api/command/{belt,hold_all,vote,strike,clear_alarms}` | `station/belt`, `station/strike`, `station/cmd` | as before |

### Pi side (changed in `acoustic_node/python/fusion/coordinator.py`)

`set_line()` and `set_feed_paused()`; `station/cmd` `line` / `feed`; status now carries
`settings.line`, `settings.feed` and `outbox_depth`; a `line_stopped` state; the `unoq`
health row warns while paused and **fails when the queue is full and tiles are being
dropped**. Documented in `station_coordination.md`.

### Demo modes

| | `--demo` (`demo.py`) | `--sim` (`sim_line.py`) |
|---|---|---|
| Coordinator, ledger, outbox, line/feed | **pretended** (re-implemented) | **real** `StationCoordinator` |
| Gantry queue / runner / reporter / commands | not present | **real** `FeedClient`, `JobRunner`, `GantryReporter`, `GantryCommands` over `SimulatedMcu` |
| Camera, Nano, microphone | fake | fake (same stand-ins) |
| Camera fault every ~50 s | yes | no |

---

## 3. Verified vs not verified

**Verified (laptop):**
- 877 tests pass; master-related: node 21, plant 17, analysis 10, troubleshoot 17, insights 15, llm 8, sim 3, feed-drain 4, coordinator 35.
- 35 checks in real headless Edge (`development/drive_master_pages.py`): no page errors; station select; view buttons; Stop/Start line and belt restore; Pause/Resume feed; hold-all; window switches; table toggle; the gantry sequencer runs the full pick-and-place cycle and a carried tile never strays > 0.05 from the cup; **no tile on the belt moves faster than 3.6 units/s** (the old behaviour measured 44).
- The feed gate, end to end on the real components (`test_feed_pause_gantry_drain.py`): a paused feed lets the gantry drain what it accepted and idle, held tiles are delivered in order and sorted exactly once on resume. Mutation-checked.
- The Hold-tile gap (finding 6) is pinned by `test_KNOWN_GAP_a_hold_tile_is_acked_then_fails...` in the same file: a *characterization* test that records current behaviour, it does not endorse it - flip it when the behaviour is decided.

**Not verified:**
- Any of it against a real MQTT broker, the Pi, the UNO Q, the Nano, a belt, a camera or the real firmware. The "gantry" is the protocol's simulated MCU.
- Clicking a station *inside the 3D canvas* (only the list is click-tested), hover tooltips.
- A real language model has never run (Ollama is installed, **no model pulled**; the Claude path has never been called).
- Tile positions in the 3D view are **derived** (age along the belt, held at the next station until it has reported), not measured.
- Detection thresholds (`insights.py`) and the diagnosis advice (`troubleshoot.py`) are reasoned, not tuned on real line data; the Nano/UNO Q advice describes firmware and apps that do not all exist yet.

---

## 4. Findings worth remembering

1. **The tile "zoom" bug (fixed `b11a505`).** On a final's arrival the tile's target jumped to the pick position and easing flung it across the belt. Tiles now move at belt speed only, appear where they are, and are speed-capped. A regression check is in the browser driver.
2. **The gantry is the bottleneck at today's placeholder config.** With the configured feed words the simulated gantry takes **~22 s per tile** against a tile graded every few seconds (queue depth reached 30+ in 20 s). This is a property of *placeholder* positions/feeds, not the machine — but it means the queue size, `max_outbox` and the belt speed need real numbers before any claim about throughput. `--sim` defaults to a fixed travel speed so it looks alive; `SimLine(real_feeds=True)` shows the real behaviour.
3. **Final = delivery.** While the feed is paused a final is stored on the Pi but is *not on the bus*, so the master sees those tiles only after resume. Consequence of the contract, logged rather than redesigned.
4. **A long pause drops tiles.** The outbox (`unoq.max_outbox`, 50 in config) drops the oldest when full and raises `outbox_overflow`; the gantry station warns, then fails, first.
5. **A hard gantry pause is a different, worse thing than the feed gate:** it freezes mid-carry with the tile on the cup (proved in `test_a_hard_pause_mid_carry...`). That is why Stop line uses the feed gate.
6. **A Hold tile is acked and then FAILS at the gantry (observed 2026-10-13, `--sim`, real config).** The station sends Hold to destination `manual` (documented as "put in the manual bin, not skip", `station_coordination.md`), but the gantry has no location of that name. On the real components the UNO Q **acks it `ok`**, then the job runner refuses it at planning time (`unknown destination 'manual'; known: ['bin_1', 'bin_2', 'bin_3', 'bin_4']`), sends **no motion**, reports `placed: failed` and raises one `gantry_pick_failed` alarm **per tile** (12 Hold tiles -> 12 alarms). It is not latched as a fault and the delivery row stays `acked`, so **nothing marks the tile as needing a person**. *Inference, not observed:* the tile is never taken off the belt, so on a real belt it would run off the end or jam. Needs a decision (section 5, item 1).
7. **The belt's real "camera → final" time** (the number `ledger.max_wait_s` should be set from) is unmeasured; the Analysis page computes it once real data exists.
8. **A full stack fails every further pick for that bin (observed).** `bin_1` holds 10 tiles (`stacks.*.max_height`); after that each Grade A tile fails with `stack 'bin_1' is full (10 tiles)` and raises an alarm. Nothing empties a stack in the sim - on the real machine an operator must empty it and send `station/gantry/cmd reset_stack`. There is no "nearly full" warning, only the failures. (The 3D page caps its drawn stack at 8; cosmetic only.)
9. **Alarm flood.** Both failures above raise one `machine/alarm` per tile; at a tile every few seconds that is dozens a minute. Troubleshoot groups by kind, but the Pi's alarm list and any operator display will scroll. Consider edge-triggered / rate-limited alarms for repeated identical failures.
10. **Test artifact, not a finding:** stopping `SimLine` publishes the gantry's offline last-will, so a collector read *after* `stop()` shows the gantry `level: fail` with empty fields. Read status before stopping.

---

## 5. To do — next session, in rough order

**Do first (small, unblocking)**
1. **Hold -> `manual` (observed, needs a decision):** the gantry acks a Hold tile and then fails it (finding 6). Choose one, implement it and flip the known-gap test: (a) give the gantry a physical hold tray - add a `manual` / `bin_hold` location to `pick_place_node/python/pick_place/config.yaml` so Hold tiles are placed (and state how many it holds); (b) the Pi does not send Hold finals to the gantry at all and raises ONE alarm "tile X needs a person"; or (c) the gantry acks Hold as "manual - not picked" and the Pi marks the tile `needs_person` instead of `acked`. Either way a Hold tile must end up *visibly* needing a person, not as a quiet `pick_failed`.
1b. **Stack-full handling (finding 8):** a nearly-full warning (e.g. at 80 %), a full stack as ONE sticky alarm rather than one per tile, and stack fill shown in the master (the gantry already reports `stacks` in its status).
1c. **Rate-limit identical gantry alarms (finding 9).**
2. **Whole-line stop should also tell the 3D/Analysis views it is stopped** (the pill and state exist; check the Analysis "throughput" note for a deliberately stopped line so it is not read as a fault).
3. **Click-select inside the 3D canvas** in the browser driver (project a station to a pixel and click it).
4. **Move or name `hair_res.json`** (loose at the repo root; generator unknown) and decide the fate of `AGENTS.md` (a stale Codex copy of CLAUDE.md).

**Decisions needed from the owner**
5. What should "stop the line" cover beyond belt + striker + hold + feed? (e.g. the camera? a hard e-stop is wired separately and out of scope.)
6. `unoq.max_outbox` and what a very long stop should do: drop oldest (today), refuse new, or stop the belt itself.
7. "core xyz": I built a standard **Cartesian** gantry. If CoreXY (belts) was meant, the model and the control protocol differ.
8. The pick-and-place twin got the master's look (`89c260f`); decide whether its remaining panels should match too.

**Bigger**
9. **First real broker run:** master on the laptop against the Pi's mosquitto with the real station service (`documents/programming/station_coordination.md` §6–7 bring-up checklist).
10. **Gantry station on the UNO Q** (`pick_place.gantry_station`), then repeat the feed-drain check with real firmware.
11. **Per-tile `placed` / `pick_failed` in the master** (the topics are already collected): show placement outcome and stack counts from the gantry instead of the derived visuals.
12. **Persist health history across master restarts** (today it starts empty each run) and add the per-station uptime to Analysis.

**Parked — AI insights** (own checklist in `TODO.md`): pull a model and test faithfulness, decide small-local vs Claude API, tune thresholds on real data, FR-xx entries, Phase 2 adaptive grading untouched.

---

## 6. Commits of 2026-10-12/13 (this work)

| Commit | What |
|---|---|
| `4a6ab40` | Plant 3D view, line control and demo line |
| `cbb1729` `3f491ad` | Analysis model + page |
| `2cdd09a` `78609de` | Health history, troubleshooting rules + page |
| `89defa9` `82349c2` | Gantry rebuilt as 4-leg XYZ; beam fixed and tiles ride the cup |
| `22b2031` `22094a7` `18824fa` | AI insights: rules, LLM explainer, page (parked) |
| `776ed8e` `bdad6e1` | Parked-note docs; real-browser driver |
| `4eadbee` `2e29a34` | Whole-line soft stop/start (Pi + master) |
| `89c260f` | Pick-and-place twin restyled |
| `a391e6a` `ebcd14a` `7429818` | Gantry feed gate (Pi + master) + end-to-end drain test |
| `b11a505` | Tile zoom fix |
| `25a03c6` | `--sim` demo |
| `8aca02e` `e3fdb01` `d2c52c0` `e1f0b9f` | Owner's pre-existing work committed on request: corner shape checks, Pi scripts, AGENTS.md, hair_res.json |
| `657f665` | This document, TODO / CLAUDE.md / README pointers |
| (this revision) | Hold-tile finding, known-gap test, doc update |

Other commits in that range (`6075980`, `a5ba38e`, `1089931`, `fd39ef4`, `fcf2a3f`) were made
by a parallel session (gantry station link, Nano firmware core, logbook) and are not part of
this document.
