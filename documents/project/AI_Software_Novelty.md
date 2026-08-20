# Software-Level Novelty: AI Diagnostics + Adaptive Grading

**Status:** thinking stage — no code, no design commitments yet. Notes captured
2026-08-20, the day after Review 1 (2026-08-19), where the guide/panel liked the overall
idea but flagged the project as short on novelty. This document is the response: two
software-level additions layered on top of the existing algorithmic pipeline, plus a
phased plan for building them without blocking the working baseline.

Related: `project_charter.md` (system source of truth), `Automation_Architecture.md`
(distributed compute/monitoring architecture this slots into), `requirements.md` (formal
FR/NFR spec — neither feature below has an FR number yet), `TODO.md` line 39 ("Database
logging + dashboard — not started", which idea 1 depends on).

---

## 1. Idea A — AI-logged anomaly diagnosis (LLM-based fault summarization)

**What:** every station (acoustic, camera, pick-and-place) already produces — or will
produce, once the control/logging layer exists — structured events: homing results,
motion/position errors, timing faults, sensor dropouts, retries. Instead of just logging
these for a human to scroll through, periodically (or on-demand) feed the recent event
log to an LLM and get back a plain-English diagnostic summary: what went wrong, likely
cause, whether it's a one-off or a pattern (e.g. "Z-axis homing failed 3 times in the
last 20 cycles, all preceded by a vacuum-toggle event — check the solenoid timing").

**Why this is novel enough to matter:** turns raw telemetry (which any automation system
has) into an analysis/diagnosis layer (which most student projects don't attempt). Also
directly answers "results & analysis" and "technical quality" rubric criteria for future
reviews, since it's a visible, demoable feature rather than an internal implementation
detail.

**Dependencies / prerequisites:**
- A real structured event log exists somewhere (currently nothing is persisted — see
  `TODO.md` "Database logging + dashboard (charter §9.4, §14) — not started"). This has
  to land first; the LLM layer is a consumer of that log, not a replacement for it.
- Needs an actual control layer producing *meaningful* fault events (homing, motion,
  timing) — today only the camera node's tile-grading events exist; acoustic/pick-place
  don't generate control-level events yet because their hardware/control code doesn't
  exist yet either.

**Rough execution plan:**
1. Define a minimal structured event schema (timestamp, station, event type, severity,
   context fields) — this is really just the database logging layer from `TODO.md`,
   scoped to include fault/anomaly events, not only grading records.
2. Land that logging layer first (per-station events → shared log/DB), independent of any
   AI work — it's useful on its own for debugging during bring-up.
3. Add a summarization pass: batch the recent log window, send to an LLM with a fixed
   prompt template (system context: what this machine is, what "normal" looks like), get
   back a short diagnostic write-up. Start as an on-demand CLI/dashboard button, not a
   background job — cheaper to build, easier to demo, avoids API-cost/rate questions
   during a class project.
4. Surface it in whichever dashboard ends up being the "master" one (charter §14
   Monitoring Architecture) once that exists.

**Risk:** low-to-moderate. The hard prerequisite (real event logging) is already planned
work, not new scope. The LLM part itself is a thin, well-understood pattern (log →
prompt → summary) — the main risk is scope creep into things like auto-remediation, which
should stay explicitly out of scope for now.

---

## 2. Idea B — AI-based adaptive grading (algorithmic first, adaptive later)

**What:** two-phase plan for how tiles actually get graded.

- **Phase 1 (current, keep as-is):** the existing rule-based pipeline —
  `camera_node/python/camera/pipeline.py`'s `grade_tile()` plus whatever the acoustic and
  dimensional stations eventually contribute — stays the working baseline. This is
  "algorithm-based sorting using the existing analysis system," already built for the
  camera side.
- **Phase 2 (future, exploratory):** an AI/ML-based grading model that can *adapt on the
  machine* to changing conditions — different clay mixtures, tile shapes/sizes — without
  requiring a pre-collected labeled training set up front. Motivation: SMTW-style
  manufacturers don't have a fixed, uniform product line (clay mixture and tile
  geometry vary), so a model trained once on one batch of tiles may not generalize; an
  adaptive system that adjusts its own thresholds/decision boundaries from what it
  observes in production is more industrially useful than a static classifier.

**Why phase it this way (explicit tradeoff):** a fixed rule-based system is
understandable, testable, and demoable today — it's what the current pipeline already
does, and it should stay the fallback/reference regardless of how far Phase 2 gets. An
adaptive, no-pretraining-required model is the higher-novelty, higher-risk piece: getting
an online/unsupervised learner to reliably distinguish "this is a real defect" from "this
is normal variation in a new clay batch," without labeled ground truth, is a genuinely
hard problem — not just an engineering integration task. Treat Phase 2 as a stretch
research goal to discuss openly with the guide, not a committed deliverable, so review
expectations stay calibrated.

**Rough execution plan:**
1. Keep Phase 1 (rule-based) as the system of record for grading; every station's
   detector output (crack length/severity, corner fill-ratio/missing-extent, eventually
   acoustic FFT features and dimensional measurements) already produces numeric features
   — these become the feature vector Phase 2 would consume, so no separate feature-
   engineering step is needed to start experimenting.
2. Before touching "no pretraining" adaptive learning, do an intermediate step: train a
   conventional classifier offline on whatever labeled tile photos/acoustic clips exist
   (once real damaged-tile data is collected — see the camera node's Known Technical Debt
   in `.claude/CLAUDE.md`: currently zero labeled broken-corner examples, one labeled
   crack example). This validates the feature set and gives a fallback ML baseline even
   if the adaptive piece stalls.
3. Investigate adaptive/online approaches suited to "no labeled data, must adapt in
   production": candidates to evaluate (not decided) include online/incremental
   clustering (flag statistical outliers relative to a rolling window of recent tiles as
   the adaptive "normal"), semi-supervised approaches seeded by a human confirming a
   handful of grades per shift, or drift-detection on top of the Phase 1 rule thresholds
   (auto-adjust thresholds as the observed distribution shifts, rather than a full
   black-box model). Pick one to prototype only after Phase 1 is solid and real tile data
   (multiple clay batches, ideally) exists to test adaptation against.
4. Any Phase 2 model runs *alongside* Phase 1's rule-based grade, not as an unquestioned
   replacement, at least until it's been validated against the rule-based baseline on
   real data — mirrors how `corner_detection.py`'s two independent checks were combined
   rather than trusting one blindly.

**Risk:** moderate (classifier-on-existing-features step) to high (true label-free
adaptive learning). The biggest blocker isn't compute or code, it's data: there are no
labeled defect photos yet (camera) and no real acoustic tap data yet (acoustic
calibration is still a placeholder — see `.claude/CLAUDE.md` Known Technical Debt). Both
ideas below depend on the underlying sensing pipelines actually running against real
tiles first.

---

## 3. Sequencing relative to existing TODO

Neither idea should block or reorder the hardware/control-layer work already in
`TODO.md` — if anything, Idea A's prerequisite (structured event logging) *is* already
planned work (`TODO.md` line 39), just reframed to explicitly include fault/anomaly
events, not only grading records. Suggested order:

1. Continue current Phase 1 hardware/pipeline work (dimensional module, control layer,
   pick-and-place) — unchanged priority.
2. When the database/logging layer gets built, scope it to cover fault events too (small
   addition, same piece of work) — unlocks Idea A.
3. Build Idea A's LLM summarization pass once real events exist to summarize — cheap,
   fast, good novelty-per-effort, safe to demo at the next review.
4. Treat Idea B as a longer-running research thread: start collecting labeled
   defect/damage data opportunistically as soon as real tiles are available (this was
   already a blocker for validating the *existing* rule-based detectors — see camera
   node Known Technical Debt — so it serves both purposes), and only start prototyping
   the adaptive model once that data exists.

## 4. Open questions (unresolved as of 2026-08-20)

- Which LLM/API to use for Idea A's summarization, and whether it needs to run
  on-machine (offline) or can call out to a hosted API — affects cost, latency, and
  whether it fits the "industrial deployment" story from the charter.
- Where the line is between "adaptive thresholds" (Idea B's lower-risk end) and "true
  online learning" (higher-risk end) — needs a decision once real multi-batch tile data
  exists to actually test adaptation against.
- Whether either idea needs its own FR-xx entries in `documents/requirements/
  requirements.md`, or stays a design-notes-only exploration until scope is firmer.
- How to present Phase 2 (Idea B) to the guide without overcommitting — frame as
  research direction/stretch goal, confirm how much weight "in-progress novelty" carries
  vs. "working novelty" for remaining review rubrics.
