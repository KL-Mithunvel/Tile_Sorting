# Master GUI — handover (written 2026-10-12/13; last revised end of 2026-10-13)

One page to pick the master GUI work up from: what exists, how to run it, what is and is
not verified, what we learned, and what is next. The per-feature detail lives in
`master_node/README.md`; the message contract in `station_coordination.md`.

## Start here tomorrow

**Where things stand (end of 2026-10-13).** Everything below is committed and **pushed to `origin/main`**
(`64de570`, a fast-forward; 895 tests pass, 82 skipped; the real-browser driver passes 39/39 against both
`--demo` and `--sim`). Nothing is uncommitted. The checkout is on `main`; the branch `gantry-station-link`
still exists locally and is fully merged into `main` (its copy on GitHub is behind and was not pushed). This
handover commit itself is one commit ahead of `origin/main` until it is pushed.

**Done today, in one breath:** master GUI with four views; Stop/Start line; Pause/Resume feed; gantry hard pause;
4-leg Cartesian XYZ gantry that picks and carries each tile; a master PC + operator in the 3D scene;
`--demo` and `--sim` (real coordinator + real gantry runner on a simulated machine); Hold tiles kept off the
gantry (they pause the feed until a person presses Resume feed); one alarm per cause at the gantry;
the pick-and-place twin restyled; the owner's corner-shape checks committed.

**First three things to do** (details in section 5):
1. **The Pi's own kiosk screen has no hold-tile prompt** - only the master shows "N HOLD TILE(S) NEED A PERSON".
   On the real line the person is standing at the Pi, not at the laptop. Add it to `acoustic/templates/station.html`.
2. **Nearly-full stack warning** - a full stack still fails every further pick for that bin (one alarm now, but no
   early warning and nothing in the UI).
3. **Decide what the belt does with tiles queued behind a hold tile** at the pickup (blocked on the pickup/stopper
   design - ask before building).

**Questions waiting for the owner:** (a) "core xyz" - standard Cartesian (built) or CoreXY? (b) should a *line stop*
make every tile in flight a Hold (it does today) or should in-flight tiles be allowed to finish? (c) `max_outbox`
behaviour on a very long stop (drops the oldest today). (d) what to do with `AGENTS.md` (stale Codex copy of
CLAUDE.md) and `hair_res.json` (loose root file, generator unknown).

**Read before changing the gantry/Hold logic:** finding 6 below - the gantry picks whatever is at ONE fixed pickup
point, so any tile it is not told about is mis-sorted into the next bin. Several design rules follow from that.

---

**One-line status:** the master (`master_node/`) has four views (Plant 3D now includes the master PC and the operator), a line/feed/gantry control
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
OPENBLAS_NUM_THREADS=1 python -m pytest tests -q     # 895 passed, 82 skipped on 2026-10-13
python -m master.live_master --sim                   # (in master_node/python) then, from the repo root:
python development/drive_master_pages.py             # 39 checks in a real headless Edge
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
- Hold tiles (finding 6), end to end on the real components: the tile ahead is sorted, the hold tile is never a pick job, the tile behind it is held back, the gantry sits idle (no motion), and Resume feed lets the next tile be sorted in order (`test_a_hold_tile_never_reaches_the_gantry_and_stops_the_feed...`); plus the alert/health/confirmation/unknown-tile/clear-alarms cases in `test_fusion_coordinator.py`.

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
6. **Hold tiles — FIXED 2026-10-13 (owner chose "Pi keeps Hold off the gantry").** *What was wrong:* the Pi sent Hold to destination `manual`, which the gantry has no location for - it acked the final, then refused the job (`unknown destination 'manual'`), sent no motion and raised one alarm per tile. *Worse than first thought:* the gantry always picks from ONE fixed pickup point with no per-tile identification (`plan_pick_place(source, ...)`), so any tile it is not told about is picked into the NEXT tile's bin - every tile after a Hold would be off by one. *What happens now:* a Hold final is never published on `tile/<id>/final`; the tile is marked `delivery=needs_person`, announced on `tile/<id>/needs_person` (the master reads it as the tile's final), raises ONE sticky alert (a count), and - `final.hold_pauses_feed`, default true - **pauses the gantry feed** so nothing behind it can be picked. A person takes it off the belt and presses **Resume feed**; **starting the line does not confirm it** (the feed stays paused if hold tiles are pending). `final.hold_to_gantry: true` restores the old behaviour. *Consequences to know:* stopping the line makes every tile in flight a Hold, so a stop leaves hold tiles to clear; a run of Holds (no-tap timeouts, hold-all left on) stalls sorting until someone clears them - by design, visibly, rather than silently mis-sorting. *Not decided / unknown:* what the belt does with the tiles queued BEHIND a hold tile at a physical pickup stopper (the stopper design is open).
7. **The belt's real "camera → final" time** (the number `ledger.max_wait_s` should be set from) is unmeasured; the Analysis page computes it once real data exists.
8. **A full stack fails every further pick for that bin (observed).** `bin_1` holds 10 tiles (`stacks.*.max_height`); after that each Grade A tile fails with `stack 'bin_1' is full (10 tiles)` and raises an alarm. Nothing empties a stack in the sim - on the real machine an operator must empty it and send `station/gantry/cmd reset_stack`. There is no "nearly full" warning, only the failures. (The 3D page caps its drawn stack at 8; cosmetic only.)
9. **Alarm flood — FIXED 2026-10-13 (owner chose "one sticky alarm per cause").** The gantry reporter now raises one `gantry_pick_failed` alarm per *cause* (the reason text), re-publishes a count at most every 10 s, forgets a cause quiet for 60 s, and always gives a tile left ON THE CUP its own alarm. Every tile still gets its own `tile/<id>/placed` report. The Pi's `needs_person` alert is sticky the same way.
10. **Test artifact, not a finding:** stopping `SimLine` publishes the gantry's offline last-will, so a collector read *after* `stop()` shows the gantry `level: fail` with empty fields. Read status before stopping.

---

## 5. To do — next session, in rough order

**Do first (small, unblocking)**
0. **Hold-tile prompt on the Pi's kiosk** (`acoustic_node/python/acoustic/templates/station.html`, `dashboard.py`): show the `needs_person` count and a Resume-feed button there (it is already a control on the Pi's service view). `needs_person` and `needs_person_tiles` are already in the coordinator snapshot.
0b. **Resume feed should say what it is confirming.** The button is the same one used to resume after a plain pause; when hold tiles are pending it should read "Hold tiles taken off the belt - resume" (master and kiosk).
1. ~~Hold -> `manual`~~ **Done 2026-10-13** (finding 6): Hold stays off the gantry; a Hold pauses the feed; Resume feed confirms. *Follow-ups:* (a) decide what the belt does with tiles queued behind a hold tile at the pickup; (b) decide whether a Hold during a *line stop* should really each need a person (it does today: in-flight tiles become Hold); (c) show hold tiles and the Resume-feed prompt on the Pi's own kiosk screen, not only the master; (d) the master's Analysis counts a Hold's destination as `manual` - fine, but it reads as a bin.
1b. **Stack-full handling (finding 8):** a nearly-full warning (e.g. at 80 %), a full stack as ONE sticky alarm rather than one per tile, and stack fill shown in the master (the gantry already reports `stacks` in its status).
1c. ~~Rate-limit identical gantry alarms (finding 9)~~ **Done 2026-10-13** (one alarm per cause).
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
| `5e86064` | Hold-tile finding + known-gap test |
| `139c1b4` `caeb380` | Master PC + operator in the 3D scene; driver hardening |
| `aa1b3d8` `a847f34` | Hold stays off the gantry + feed pause; one gantry alarm per cause |
| `f0b5481` | Start line does not confirm hold tiles are off the belt (Pi) |
| `70175b7` | Master shows hold tiles waiting for a person; docs |
| `64de570` | Merge of `origin/main` (PR #1) - pushed to `main` |
| (this revision) | End-of-day handover |

Other commits in that range (`6075980`, `a5ba38e`, `1089931`, `fd39ef4`, `fcf2a3f`) were made
by a parallel session (gantry station link, Nano firmware core, logbook) and are not part of
this document.

---

## 7. Git state and a caution (2026-10-13)

- `origin/main` = `64de570` = local `HEAD`; the push was a fast-forward, no force.
- All of this work was committed on `gantry-station-link`; the checkout was later moved to `main` (reflog: `checkout:
  moving from gantry-station-link to main`, then a rebase onto `origin/main` that was a no-op). The remote copy of
  `gantry-station-link` was not updated; to tidy, `git push origin gantry-station-link` (fast-forward) or delete it.
- **A published commit was amended.** `8fc590a` (the browser driver's wait-out-the-fault change) was already in PR #1
  when it was amended locally into `caeb380`; the merge needed one conflict resolution in
  `development/drive_master_pages.py` (kept the superset). Don't amend commits that have been pushed.
- A parallel Claude session also committed to this repo during these two days (gantry station link, Nano firmware
  core, logbook). Those commits (`6075980`, `a5ba38e`, `1089931`, `fd39ef4`, `fcf2a3f`) are in the history and are
  not described here; `git log --oneline 4a6ab40^..HEAD` shows the interleaving.
- Nothing has been run against real hardware. Every statement above about the gantry, the Pi and the line is about
  the laptop, the in-memory bus and the simulated machine.
