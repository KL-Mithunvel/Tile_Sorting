# Review 1 — System Flowcharts & Architecture Diagrams

**Due: 2026-08-19 (tentative)** · supports `Review_1_Content.md` §2 (Methodology,
Technical Design & Feasibility) and §3 (Implementation, Progress & Technical Quality)

Every diagram below is redrawn from what's already decided in
`documents/project/project_charter.md` and `documents/project/Automation_Architecture.md`
— nothing here is new design. All diagrams are Mermaid, which GitHub renders natively in
this file; nothing is a generated image. **Each diagram carries its own title bar**, so
you can screenshot just the rendered diagram block and drop it straight into a slide
without also capturing the surrounding text. Where a component is actually built vs.
still planned, it's marked explicitly:

- 🟢 **Built & tested** — real, working code exists (`camera_node/python/camera/`,
  `acoustic_node/python/acoustic/`)
- 🟡 **Scaffolding only** — folder/App Lab stub exists, no real logic yet
  (`pick_place_node/`)
- ⚪ **Not started / open decision** — no code, hardware not finalized (measurement node,
  conveyor controller)

Status is as of 2026-08-18 (`TODO.md` / `.CLAUDE/CLAUDE.md`).

---

## Contents

1. [System Architecture — Compute Nodes](#1-system-architecture--compute-nodes)
2. [Physical Station Layout on the Line](#2-physical-station-layout-on-the-line)
3. [Control Network — Ethernet/MQTT Topology](#3-control-network--ethernetmqtt-topology)
   - [3b. Network Wiring — IP Address Assignment](#3b-network-wiring--master-switch--every-nodes-ip-address)
4. [Network Protocol Stack](#4-network-protocol-stack)
5. [MQTT Topic Map](#5-mqtt-topic-map)
6. [End-to-End Data Flow (Local-Process-Then-Publish Principle)](#6-end-to-end-data-flow-local-process-then-publish-principle)
7. [Full Tile Process Flow](#7-full-tile-process-flow)
8. [Generic Station Handshake (Sequence)](#8-generic-station-handshake-sequence)
9. [Camera Station — Detailed Sequence](#9-camera-station--detailed-sequence)
10. [Acoustic Station — Detailed Sequence](#10-acoustic-station--detailed-sequence)
11. [Measurement Station — Detailed Sequence](#11-measurement-station--detailed-sequence)
12. [Final Grade Fusion Logic](#12-final-grade-fusion-logic)
13. [Tile Digital Record — State Lifecycle](#13-tile-digital-record--state-lifecycle)
14. [Database Entity Relationships](#14-database-entity-relationships)
15. [Failure Handling & Safety Rules](#15-failure-handling--safety-rules)
16. [Node Heartbeat / Health Monitoring](#16-node-heartbeat--health-monitoring)
17. [Phased Build Plan vs. Current Status](#17-phased-build-plan-vs-current-status)
18. [Prototype → Industrial Upgrade Path](#18-prototype--industrial-upgrade-path)

---

## 1. System Architecture — Compute Nodes

Distributed architecture: each inspection station is its own compute node that processes
its own raw sensor data locally and reports only a compact result to the master.
Source: `Automation_Architecture.md` §2, §5.

```mermaid
---
title: "1. System Architecture — Compute Nodes"
---
flowchart TB
    MASTER["🔵 MASTER PC — user's own PC<br/>Tile ID Manager · SQLite DB<br/>Final Grade Fusion · Dashboard/HUD<br/>Robot Command Generator"]

    subgraph STATIONS["Inspection Stations (independent compute nodes)"]
        direction LR
        CAM["🟢 Camera Node<br/>Arduino UNO Q<br/>camera_node/"]
        ACO["🟢 Acoustic Node<br/>Arduino UNO Q<br/>acoustic_node/"]
        MEA["⚪ Measurement Node<br/>hardware not yet decided"]
    end

    CONV["⚪ Conveyor / Motion Controller<br/>Arduino Mega — not App Bricks"]
    ROBOT["🟡 Pick-and-Place Controller<br/>Arduino UNO Q<br/>pick_place_node/"]

    MASTER <-->|"MQTT / wired Ethernet"| CAM
    MASTER <-->|"MQTT / wired Ethernet"| ACO
    MASTER <-->|"MQTT / wired Ethernet"| MEA
    MASTER <-->|"MQTT / wired Ethernet"| CONV
    MASTER <-->|"MQTT / wired Ethernet"| ROBOT

    CONV -.->|"physically carries tile"| CAM
    CAM -.->|"physically carries tile"| ACO
    ACO -.->|"physically carries tile"| MEA
    MEA -.->|"physically carries tile"| ROBOT

    classDef built fill:#bbf7d0,stroke:#166534,color:#052e16;
    classDef scaffold fill:#fef08a,stroke:#854d0e,color:#422006;
    classDef planned fill:#e5e7eb,stroke:#4b5563,color:#111827;
    classDef master fill:#bfdbfe,stroke:#1e3a8a,color:#0c1e3e;

    class MASTER master;
    class CAM,ACO built;
    class ROBOT scaffold;
    class MEA,CONV planned;
```

Only **one physical UNO Q board** exists today — the three-node-folder split is a
code-organization decision made ahead of hardware (`Automation_Architecture.md` §5.7),
not proof three boards are in hand.

---

## 2. Physical Station Layout on the Line

Encoder-based position tracking, not time-based (chosen because conveyor slip/speed
change/jams/e-stop all break a timer). Source: `Automation_Architecture.md` §8.

```mermaid
---
title: "2. Physical Station Layout on the Line"
---
flowchart LR
    E["Entry Sensor<br/>0 mm<br/>encoder: entry count"] --> C["Camera Station<br/>500 mm"]
    C --> A["Acoustic Station<br/>1000 mm"]
    A --> M["Measurement Station<br/>1500 mm"]
    M --> P["Pickup / Pick-and-Place<br/>2000 mm"]

    classDef pt fill:#fde68a,stroke:#92400e,color:#451a03;
    class E,C,A,M,P pt;
```

```text
tile position = current_encoder_count − entry_encoder_count
```

The controller uses this to predict exactly when a given `tile_id` reaches each station,
even with multiple tiles queued on the conveyor at once (Phase 3, §21).

---

## 3. Control Network — Ethernet/MQTT Topology

Wired Ethernet star topology, MQTT broker (Mosquitto) hosted on the master PC.
Source: `Automation_Architecture.md` §12, §23.

```mermaid
---
title: "3. Control Network — Ethernet/MQTT Topology"
---
flowchart TB
    MASTER["🔵 Master PC — 192.168.1.10<br/>Mosquitto MQTT Broker · Dashboard · SQLite DB"]
    SWITCH{{"Ethernet Switch"}}

    MASTER --- SWITCH
    SWITCH --- CAM["🟢 Camera Node<br/>192.168.1.11"]
    SWITCH --- AUD["🟢 Audio Node<br/>192.168.1.12"]
    SWITCH --- MEA["⚪ Measure Node<br/>192.168.1.13"]
    SWITCH --- CONV["⚪ Conveyor Ctrl<br/>192.168.1.14"]
    SWITCH --- ROB["🟡 Robot Ctrl<br/>192.168.1.15"]

    classDef built fill:#bbf7d0,stroke:#166534,color:#052e16;
    classDef scaffold fill:#fef08a,stroke:#854d0e,color:#422006;
    classDef planned fill:#e5e7eb,stroke:#4b5563,color:#111827;
    classDef master fill:#bfdbfe,stroke:#1e3a8a,color:#0c1e3e;
    classDef sw fill:#ddd6fe,stroke:#5b21b6,color:#3b0764;

    class MASTER master;
    class CAM,AUD built;
    class ROB scaffold;
    class MEA,CONV planned;
    class SWITCH sw;
```

**Why wired Ethernet, not Wi-Fi:** reliability on the production floor. **Why MQTT, not
OPC UA/Modbus for the prototype:** lightweight, broker-based, trivial to prototype in
Python — §18 below maps each of these straight to an industrial equivalent later, so this
choice doesn't force a rewrite to scale up.

### 3b. Network Wiring — Master, Switch & Every Node's IP Address

Same network, drawn as a wiring/IP-assignment sheet: every device that hangs off the
switch, labeled by exactly what hardware it is and its address. Three of the five station
devices are Arduino UNO Q boards (§5.7); the other two are the Arduino Mega conveyor
controller and the still-undecided measurement node.

```mermaid
---
title: "3b. Network Wiring — IP Address Assignment"
---
flowchart TB
    MASTER["🔵 MASTER PC<br/>192.168.1.10<br/>MQTT Broker · Dashboard · SQLite DB"]
    SWITCH{{"Ethernet Switch"}}
    MASTER ---|"eth0"| SWITCH

    CAMUNO["🟢 UNO Q #1 — camera_node/<br/>role: Camera<br/>192.168.1.11"]
    ACOUNO["🟢 UNO Q #2 — acoustic_node/<br/>role: Acoustic<br/>192.168.1.12"]
    MEAHW["⚪ Measurement Node<br/>hardware not yet decided<br/>192.168.1.13"]
    MEGAHW["⚪ Arduino Mega<br/>role: Conveyor / Motion Ctrl<br/>192.168.1.14"]
    ROBUNO["🟡 UNO Q #3 — pick_place_node/<br/>role: Pick-and-Place<br/>192.168.1.15"]

    SWITCH ---|"port 1"| CAMUNO
    SWITCH ---|"port 2"| ACOUNO
    SWITCH ---|"port 3"| MEAHW
    SWITCH ---|"port 4"| MEGAHW
    SWITCH ---|"port 5"| ROBUNO

    NOTE["⚠️ Only ONE physical UNO Q board exists today.<br/>UNO Q #1/#2/#3 are three planned roles for<br/>App Bricks code, not three boards in hand<br/>(Automation_Architecture.md §5.7)"]
    ROBUNO ~~~ NOTE

    classDef built fill:#bbf7d0,stroke:#166534,color:#052e16;
    classDef scaffold fill:#fef08a,stroke:#854d0e,color:#422006;
    classDef planned fill:#e5e7eb,stroke:#4b5563,color:#111827;
    classDef master fill:#bfdbfe,stroke:#1e3a8a,color:#0c1e3e;
    classDef sw fill:#ddd6fe,stroke:#5b21b6,color:#3b0764;
    classDef note fill:#fff7ed,stroke:#c2410c,color:#7c2d12;

    class MASTER master;
    class CAMUNO,ACOUNO built;
    class ROBUNO scaffold;
    class MEAHW,MEGAHW planned;
    class SWITCH sw;
    class NOTE note;
```

| Device | Role | IP | Hardware | Status |
|---|---|---|---|---|
| Master PC | Broker, DB, fusion, dashboard | 192.168.1.10 | User's own PC | 🔵 Confirmed |
| UNO Q #1 | Camera Node | 192.168.1.11 | Arduino UNO Q | 🟢 Built & tested |
| UNO Q #2 | Acoustic Node | 192.168.1.12 | Arduino UNO Q | 🟢 Built & tested |
| — | Measurement Node | 192.168.1.13 | Not yet decided | ⚪ Open decision |
| — | Conveyor / Motion Ctrl | 192.168.1.14 | Arduino Mega | ⚪ Not started |
| UNO Q #3 | Pick-and-Place Ctrl | 192.168.1.15 | Arduino UNO Q | 🟡 Scaffolding only |

---

## 4. Network Protocol Stack

How a station result actually gets from a node to the master, layer by layer.

```mermaid
---
title: "4. Network Protocol Stack"
---
flowchart TB
    L4["Application layer<br/>MQTT pub/sub · JSON payloads, keyed by tile_id"]
    L3["Transport layer<br/>TCP (MQTT's default transport)"]
    L2["Network layer<br/>IPv4, static addressing (192.168.1.x)"]
    L1["Physical layer<br/>Wired Ethernet, switch-based star topology"]

    L4 --> L3 --> L2 --> L1

    classDef layer fill:#e0e7ff,stroke:#3730a3,color:#1e1b4b;
    class L4,L3,L2,L1 layer;
```

---

## 5. MQTT Topic Map

Every payload is compact JSON keyed by `tile_id` — never a raw frame, waveform, or point
cloud. Source: `Automation_Architecture.md` §4, §12–§13, §20.

```mermaid
---
title: "5. MQTT Topic Map"
---
flowchart LR
    BROKER(["MQTT Broker<br/>(Mosquitto on Master PC)"])

    BROKER --- T1["tile/new"]
    BROKER --- T2["tile/&lt;id&gt;/camera/result"]
    BROKER --- T3["tile/&lt;id&gt;/acoustic/result"]
    BROKER --- T4["tile/&lt;id&gt;/measurement/result"]
    BROKER --- T5["tile/&lt;id&gt;/final"]
    BROKER --- T6["machine/conveyor/status"]
    BROKER --- T7["machine/robot/command"]
    BROKER --- T8["machine/robot/status"]
    BROKER --- T9["machine/alarm"]
    BROKER --- T10["production/counts"]
    BROKER --- T11["machine/node/&lt;node&gt;/heartbeat"]

    classDef topic fill:#f1f5f9,stroke:#475569,color:#0f172a;
    class T1,T2,T3,T4,T5,T6,T7,T8,T9,T10,T11 topic;
```

---

## 6. End-to-End Data Flow (Local-Process-Then-Publish Principle)

The core design rule (`Automation_Architecture.md` §4): raw sensor data never leaves its
station. Each node reduces its own raw data to a small structured result before it ever
touches the network.

```mermaid
---
title: "6. End-to-End Data Flow"
---
flowchart LR
    subgraph CAMNODE["Camera Node"]
        C1["Raw image frame"] --> C2["Vision pipeline<br/>(segment → crack/corner detect)"] --> C3["visual_grade + confidence"]
    end
    subgraph ACONODE["Acoustic Node"]
        A1["Raw audio waveform"] --> A2["Trigger + FFT pipeline<br/>(RMS, dominant freq, damping)"] --> A3["acoustic_grade + confidence"]
    end
    subgraph MEANODE["Measurement Node"]
        M1["Raw ToF/laser samples"] --> M2["Dimension check<br/>(4 corners + center vs. tolerance)"] --> M3["dimension_grade"]
    end

    C3 --> BROKER(["MQTT Broker"])
    A3 --> BROKER
    M3 --> BROKER
    BROKER --> MASTER["Master PC<br/>fuses results, never touches raw frames/waveforms/point clouds"]

    classDef built fill:#bbf7d0,stroke:#166534,color:#052e16;
    classDef planned fill:#e5e7eb,stroke:#4b5563,color:#111827;
    class CAMNODE,ACONODE built;
    class MEANODE planned;
```

Example compact result actually sent over the wire:

```json
{ "tile_id": "T000001", "station": "camera", "grade": "A", "confidence": 0.94 }
```

---

## 7. Full Tile Process Flow

End-to-end path of one tile, from entry sensor to sorted/stacked output.
Source: `Automation_Architecture.md` §10–§11.

```mermaid
---
title: "7. Full Tile Process Flow"
---
flowchart TD
    START(["Tile enters conveyor"]) --> ENTRY["Entry sensor detects tile"]
    ENTRY --> TID["Master creates Tile ID"]
    TID --> C1["Conveyor moves tile to Camera Station"]
    C1 --> CAM["🟢 Camera Node: capture image,<br/>detect cracks/corner/chip/glaze/shade,<br/>compute visual grade"]
    CAM --> CAMPUB["Publish visual grade to master"]
    CAMPUB --> C2["Conveyor moves tile to Acoustic Station"]
    C2 --> ACOTRIG["⚪ Laser/ToF trigger fires ball-drop impactor"]
    ACOTRIG --> ACOREC["🟢 Mic records tap sound"]
    ACOREC --> ACOPROC["🟢 FFT + resonance/damping feature<br/>extraction, compute acoustic grade"]
    ACOPROC --> ACOPUB["Publish acoustic grade to master"]
    ACOPUB --> C3["Conveyor moves tile to Measurement Station"]
    C3 --> MEA["⚪ ToF/laser: length, width, thickness,<br/>flatness, warpage — 4 corners + center,<br/>compute dimension grade"]
    MEA --> MEAPUB["Publish dimension grade to master"]
    MEAPUB --> FUSE["Master fuses camera + acoustic + dimension<br/>= worst grade wins"]
    FUSE --> GRADE{"Final Grade"}
    GRADE -->|A| PA["Pick-and-place → Grade A stack"]
    GRADE -->|B| PB["Pick-and-place → Grade B stack"]
    GRADE -->|C| PC["Pick-and-place → Grade C stack"]
    GRADE -->|Reject| PR["Pick-and-place → Reject / manual inspection"]
    PA --> DASH["Dashboard updates production counts"]
    PB --> DASH
    PC --> DASH
    PR --> DASH
    DASH --> END(["End / next tile"])
```

**Camera-first ordering is deliberate**: the camera node is physically first and is the
one that first announces a new tile to the master, so that tile's record starts there;
acoustic and dimensional results attach to the same `tile_id` further down the line
(`Automation_Architecture.md` §2 note, §6).

---

## 8. Generic Station Handshake (Sequence)

Every station follows the same request/acknowledge/result pattern, so the master never
has to special-case a station's timing. Source: `Automation_Architecture.md` §9.1.

```mermaid
---
title: "8. Generic Station Handshake"
---
sequenceDiagram
    participant Conv as Conveyor / Motion Ctrl
    participant Node as Station Node
    participant Master as Master PC

    Conv->>Node: Tile detected at station
    Node-->>Conv: Acknowledged
    Node->>Node: Capture + process data locally
    Node->>Master: Publish result (MQTT, tile_id keyed)
    Master->>Master: Save result to DB
    Master-->>Conv: Continue (next station / release tile)
```

---

## 9. Camera Station — Detailed Sequence

🟢 The vision pipeline itself (`camera_node/python/camera/`) is real, tested code.
Source: `Automation_Architecture.md` §5.2, §9.2.

```mermaid
---
title: "9. Camera Station — Detailed Sequence"
---
sequenceDiagram
    participant Tile as Tile (physical)
    participant Node as Camera Node (UNO Q)
    participant Master as Master PC

    Tile->>Node: Reaches camera station
    Node->>Node: Capture image
    Node->>Node: segment_tile() — isolate tile via HSV threshold
    Node->>Node: detect_cracks() + detect_broken_corner()
    Node->>Node: grade_tile() — assign visual grade
    Node->>Master: Publish visual_grade + confidence
```

---

## 10. Acoustic Station — Detailed Sequence

🟢 Everything from "Mic records" onward (`TriggerDetector`, FFT/signal processing) is
real, unit-tested code. The laser/ToF trigger and ball-drop release hardware are still
unbuilt (`sketch/` stub only). Source: `Automation_Architecture.md` §5.3, §9.3;
`project_charter.md` §6.2.

```mermaid
---
title: "10. Acoustic Station — Detailed Sequence"
---
sequenceDiagram
    participant Laser as ⚪ Laser/ToF sensor
    participant Ball as ⚪ Ball-drop impactor
    participant Mic as 🟢 Mic
    participant Node as 🟢 Acoustic Node (UNO Q)
    participant Master as Master PC

    Laser->>Node: Tile in position
    Node->>Ball: Trigger release
    Ball->>Ball: Ball strikes tile under gravity
    Mic->>Node: Record tap sound
    Node->>Node: TriggerDetector — rolling RMS vs threshold,<br/>pre-trigger buffer, cooldown
    Node->>Node: FFT (Hann window) → dominant frequency,<br/>damping score, crack probability
    Node->>Master: Publish acoustic_grade + confidence
```

`rms_threshold` is a placeholder, not yet calibrated against a real noise floor — see
`CLAUDE.md` Known Technical Debt.

---

## 11. Measurement Station — Detailed Sequence

⚪ Not started — hardware itself is still an open decision (ToF/laser array vs. camera
contour). Source: `Automation_Architecture.md` §5.4, §9.4.

```mermaid
---
title: "11. Measurement Station — Detailed Sequence"
---
sequenceDiagram
    participant Tile as Tile (physical)
    participant Node as ⚪ Measurement Node (hardware TBD)
    participant Master as Master PC

    Tile->>Node: Reaches measurement station
    Node->>Node: ToF/laser/camera measurement begins
    Node->>Node: Sample 4 corners + center<br/>(kiln shrinkage causes corner-vs-center deviation)
    Node->>Node: Compute length, width, thickness, flatness, warpage
    Node->>Node: Compute dimension_grade
    Node->>Master: Publish dimension_grade
```

---

## 12. Final Grade Fusion Logic

Source: `Automation_Architecture.md` §14–§15.

```mermaid
---
title: "12. Final Grade Fusion Logic"
---
flowchart LR
    CG["Camera Grade"] --> WORST{"Take worst grade<br/>A &lt; B &lt; C &lt; Reject"}
    AG["Acoustic Grade"] --> WORST
    DG["Dimension Grade"] --> WORST
    WORST --> FG["Final Grade"]
```

Rationale: a tile with a hidden acoustic defect must not be graded "good" just because it
passed visual inspection — worst-grade-wins is the safest rule for a first version, not
an averaged or weighted score.

---

## 13. Tile Digital Record — State Lifecycle

Every tile gets a unique ID the moment it enters the system; its record moves through
these states as each station reports in. Source: `Automation_Architecture.md` §6–§7.

```mermaid
---
title: "13. Tile Digital Record — State Lifecycle"
---
stateDiagram-v2
    [*] --> Created: Entry sensor detects tile,<br/>master assigns tile_id
    Created --> CameraDone: 🟢 Camera result received
    CameraDone --> AcousticDone: 🟢 Acoustic result received
    AcousticDone --> MeasurementDone: ⚪ Measurement result received
    MeasurementDone --> Graded: Master fuses results<br/>(worst grade wins)
    Graded --> Sorted: 🟡 Pick-and-place reports completed
    Sorted --> [*]

    CameraDone --> ManualCheck: result missing/timeout
    AcousticDone --> ManualCheck: result missing/timeout
    MeasurementDone --> ManualCheck: result missing/timeout
    ManualCheck --> [*]
```

---

## 14. Database Entity Relationships

Prototype uses SQLite. Source: `Automation_Architecture.md` §17.

```mermaid
---
title: "14. Database Entity Relationships"
---
erDiagram
    TILES ||--o{ CAMERA_RESULTS : has
    TILES ||--o{ ACOUSTIC_RESULTS : has
    TILES ||--o{ MEASUREMENT_RESULTS : has

    TILES {
        text tile_id PK
        text batch_id
        text entry_time
        text camera_status
        text acoustic_status
        text measurement_status
        text camera_grade
        text acoustic_grade
        text dimension_grade
        text final_grade
        text destination
        text current_station
    }
    CAMERA_RESULTS {
        int id PK
        text tile_id FK
        text visual_grade
        bool surface_crack
        bool corner_broken
        bool edge_chip
        real confidence
        text image_file
    }
    ACOUSTIC_RESULTS {
        int id PK
        text tile_id FK
        text acoustic_grade
        real peak_frequency_hz
        real damping_score
        real crack_probability
        real confidence
        text audio_file
    }
    MEASUREMENT_RESULTS {
        int id PK
        text tile_id FK
        real length_mm
        real width_mm
        real thickness_mm
        real flatness_mm
        text dimension_grade
    }
    PRODUCTION_COUNTS {
        int id PK
        text batch_id
        int grade_a_count
        int grade_b_count
        int grade_c_count
        int reject_count
    }
```

---

## 15. Failure Handling & Safety Rules

Source: `Automation_Architecture.md` §18–§19.

```mermaid
---
title: "15. Failure Handling & Safety Rules"
---
flowchart TD
    E{"Event"}
    E -->|"Camera / acoustic / measurement<br/>result missing"| MC["Mark tile: manual_check"]
    E -->|"Tile ID mismatch"| STOP["Stop the machine"]
    E -->|"Robot not ready"| HOLD["Hold tile at pickup buffer"]
    E -->|"Station timeout"| REJ["Send to reject / manual inspection"]
    E -->|"Database failure"| SAFE["Stop or switch to safe mode"]
```

Timeouts: camera 2s, acoustic 4s, measurement 2s, robot 5s. Emergency stop and safety
interlocks are handled by the motion controller (Mega/PLC), never the master PC alone —
the master only ever issues semantic commands, never raw actuator timing (§19 rules 6,
10).

---

## 16. Node Heartbeat / Health Monitoring

Every node publishes a heartbeat; a missing one raises a dashboard alarm.
Source: `Automation_Architecture.md` §20.

```mermaid
---
title: "16. Node Heartbeat / Health Monitoring"
---
flowchart LR
    CAM["🟢 Camera Node"] -->|"heartbeat every N sec"| BROKER(["MQTT Broker"])
    ACO["🟢 Acoustic Node"] -->|"heartbeat every N sec"| BROKER
    MEA["⚪ Measurement Node"] -->|"heartbeat every N sec"| BROKER
    CONV["⚪ Conveyor Ctrl"] -->|"heartbeat every N sec"| BROKER
    ROB["🟡 Robot Ctrl"] -->|"heartbeat every N sec"| BROKER
    BROKER --> MASTER["Master PC"]
    MASTER --> CHECK{"Heartbeat overdue?"}
    CHECK -->|Yes| ALARM["Raise dashboard alarm"]
    CHECK -->|No| OK["Node status: OK"]
```

---

## 17. Phased Build Plan vs. Current Status

Source: `Automation_Architecture.md` §21, cross-checked against `TODO.md` /
`.CLAUDE/CLAUDE.md` as of 2026-08-18.

```mermaid
---
title: "17. Phased Build Plan"
---
flowchart LR
    P1["Phase 1<br/>Single-tile manual flow<br/>(no conveyor)"] --> P2["Phase 2<br/>Conveyor, one tile at a time"]
    P2 --> P3["Phase 3<br/>Conveyor, multiple tiles<br/>(encoder tracking)"]
    P3 --> P4["Phase 4<br/>Pick-and-place integration"]
    P4 --> P5["Phase 5<br/>Packing demonstration"]

    classDef partial fill:#fef08a,stroke:#854d0e,color:#422006;
    classDef planned fill:#e5e7eb,stroke:#4b5563,color:#111827;
    class P1 partial;
    class P2,P3,P4,P5 planned;
```

**Phase 1 detail — what's actually done vs. outstanding:**

```mermaid
---
title: "17b. Phase 1 — Built vs. Outstanding"
---
flowchart TD
    A["🟢 Acoustic capture + FFT pipeline"]
    B["⚪ Real-mic calibration<br/>(--calibrate vs. actual noise floor)"]
    C["🟢 Camera / visual inspection module"]
    D["⚪ Dimensional inspection module"]
    E["⚪ Conveyor / control layer"]
    F["🟡 Database logging + dashboard<br/>(per-node dashboards exist, no master fusion dashboard yet)"]
    G["⚪ Pick-and-place mechanism"]

    classDef done fill:#bbf7d0,stroke:#166534,color:#052e16;
    classDef partial fill:#fef08a,stroke:#854d0e,color:#422006;
    classDef todo fill:#e5e7eb,stroke:#4b5563,color:#111827;
    class A,C done;
    class F partial;
    class B,D,E,G todo;
```

---

## 18. Prototype → Industrial Upgrade Path

Source: `Automation_Architecture.md` §24 — the same architecture is meant to scale up
without a rewrite.

```mermaid
---
title: "18. Prototype → Industrial Upgrade Path"
---
flowchart LR
    P1["Master PC"] --> I1["Industrial PC / SCADA server"]
    P2["MQTT"] --> I2["OPC UA / MQTT / Modbus TCP"]
    P3["Arduino / ESP32 conveyor ctrl"] --> I3["PLC"]
    P4["UNO Q / Raspberry Pi camera node"] --> I4["Industrial vision system"]
    P5["USB mic / audio interface"] --> I5["Industrial DAQ / acoustic tester"]
    P6["ToF sensor"] --> I6["Laser profiler / 3D sensor"]
    P7["DIY pick-and-place"] --> I7["Industrial robot / gantry robot"]
    P8["SQLite"] --> I8["SQL database / MES integration"]
    P9["Flask / Node-RED dashboard"] --> I9["HMI / SCADA"]
```

---

## Sources

- `documents/project/project_charter.md` — §3 Objectives, §6.1/§6.2 inspection method
  decisions, §7.4 control architecture decision
- `documents/project/Automation_Architecture.md` — full architecture, communication,
  database, and phased-plan source of truth (§2, §4–§25)
- `TODO.md`, `.CLAUDE/CLAUDE.md` — current build status as of 2026-08-18
