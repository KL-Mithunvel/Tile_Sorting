# Automated Ceramic / Terracotta Tile Sorting and Packing System

## 1. Project Idea

The proposed project is an automated tile inspection, grading, sorting, and packing system for ceramic, earthen, or terracotta tiles. The system is designed to inspect each tile using three complementary sensing methods:

1. **Visual inspection using a camera** to detect visible defects such as cracks, broken corners, chipped edges, color defects, shape irregularity, and surface damage.
2. **Acoustic inspection using controlled impact testing** to detect hidden cracks, internal defects, weak bonding, and structural inconsistency by analyzing the sound produced when a tile is struck.
3. **Dimensional inspection using time-of-flight sensors or other distance measurement sensors** to measure tile size, thickness, flatness, edge geometry, and dimensional accuracy.

After inspection, the system classifies each tile into grades and routes it automatically to the appropriate output lane, rejection bin, or packing station.

The goal is to reduce manual inspection errors, improve repeatability, increase production throughput, create traceable quality records, and support automatic packing after sorting.

---

## 2. Problem Statement

This project is proposed for Sree Murugan Tile Works, a clay tile manufacturer where
inspection, grading, sorting, and packing are presently done entirely by manual labor.

Tile production often requires manual inspection to identify defects and sort products into grades. Manual inspection has several limitations:

* Human judgment varies between operators.
* Internal cracks may not be visible to the eye.
* Repetitive inspection work causes fatigue.
* Data logging is usually incomplete or manual.
* High-speed production lines require consistent and repeatable decisions.
* Sorting and packing after inspection can become labor-intensive.

The proposed system solves these issues by combining machine vision, acoustic analysis, dimensional measurement, automated handling, and digital logging into one integrated machine.

---

## 3. Project Objectives

The main objectives of the project are:

* Automatically detect visible cracks, corner breaks, chips, shape defects, and surface defects.
* Detect hidden cracks or structural defects through acoustic response.
* Measure tile dimensions accurately and compare them with acceptable tolerance limits.
* Grade tiles into quality categories such as Grade A, Grade B, Grade C, and Reject.
* Count and log every tile passing through the system.
* Store inspection results for each tile, including image data, acoustic data, dimensional readings, grade, timestamp, and final sorting decision.
* Sort tiles automatically into different output lanes.
* Prepare sorted tiles for packing.
* Maintain repeatable inspection conditions by using controlled tile movement and a repeatable acoustic tapping mechanism.
* Reduce dependence on manual quality inspection.

---

## 4. Scope of the System

The project includes the following major functions:

### 4.1 Tile Feeding

Tiles are fed one by one into the inspection line. Feeding may be manual at the prototype stage and automated in later versions using a conveyor, magazine feeder, or pick-and-place loading mechanism.

### 4.2 Tile Positioning

Each tile must be positioned consistently before inspection. This is important for reliable camera inspection, acoustic impact location, and dimensional measurement.

Positioning may be achieved using:

* Conveyor side guides
* Mechanical stops
* Pneumatic alignment arms
* Servo-driven positioning plates
* Rollers or belts with edge alignment
* Encoder-based conveyor control

### 4.3 Visual Inspection

A camera captures one or more images of each tile. Image processing or computer vision algorithms detect visible defects and shape issues.

### 4.4 Acoustic Inspection

A controlled tapping mechanism strikes the tile with consistent force, position, angle, and contact time. A microphone records the sound. The system processes the audio signal and extracts frequency-domain features to detect abnormal acoustic signatures.

### 4.5 Dimensional Inspection

Time-of-flight sensors, laser displacement sensors, or other distance sensors measure length, width, thickness, surface height, and possibly flatness.

### 4.6 Decision and Grading

The system combines results from visual, acoustic, and dimensional inspection and assigns a grade.

### 4.7 Sorting

Based on the grade, the tile is directed to the correct lane, bin, stack, or packing station.

### 4.8 Packing Support

After sorting, tiles of the same grade can be counted, stacked, and prepared for packaging.

### 4.9 Data Logging

Every tile is recorded in a database with inspection results, grade, timestamp, and production count.

---

## 5. System-Level Architecture

The complete system can be divided into the following layers:

1. **Mechanical handling layer**
2. **Sensor layer**
3. **Actuation layer**
4. **Control layer**
5. **Computation and signal processing layer**
6. **Decision and grading layer**
7. **Sorting and packing layer**
8. **Data logging and user interface layer**

### 5.1 High-Level Architecture Diagram

```text
Tile Input
   |
   v
Feeding Conveyor / Loading Mechanism
   |
   v
Tile Alignment and Positioning Station
   |
   +-------------------------+
   |                         |
   v                         v
Camera Inspection       Dimensional Sensors
   |                         |
   +-----------+-------------+
               |
               v
        Acoustic Test Station
   Controlled Hammer / Solenoid Tapper
               |
               v
        Microphone Audio Capture
               |
               v
       Processing Computer
 Camera Analysis + Audio FFT + Dimension Analysis
               |
               v
        Grading Decision Engine
               |
               v
 Sorting Mechanism / Diverter / Pick-and-Place
               |
               v
 Grade A / Grade B / Grade C / Reject / Packing
               |
               v
 Database + Dashboard + Daily Production Report
```

---

## 6. Inspection Methods

## 6.1 Visual Inspection System

### Purpose

The camera system is used to detect defects that are visible from the tile surface or outline.

### Role as First Station (Decision, 2026-07-10)

The camera station is positioned first in the inspection sequence, ahead of acoustic and
dimensional inspection. Because of this, it is also the point where the master first
learns a tile exists: on detecting a tile, the camera node reports tile presence plus its
visual result to the master (see `Automation_Architecture.md` §2, §4), which is what
starts that tile's record. Acoustic and dimensional results are attached to the same
tile ID as the tile reaches those stations further down the line.

The camera can also derive an approximate belt speed from frame-to-frame tile motion.
This is a secondary, cross-check reading only — a dedicated conveyor encoder is the
primary and authoritative speed/position reference for tracking (see
`Automation_Architecture.md` §8, Conveyor Tracking). Do not use the camera-derived speed
as the tracking source of truth.

Open question: whether the camera also serves as the encoder-zero "entry sensor" itself,
or whether a separate dedicated entry sensor still exists just ahead of it — not decided
yet; the encoder-based tracking design in `Automation_Architecture.md` §8 works either way.

### Defects Detected

* Massive visible cracks
* Corner breakage
* Edge chipping
* Missing material
* Irregular shape
* Warping visible in top view
* Severe surface marks
* Color variation, if lighting is controlled
* Incorrect tile orientation
* Major dimensional shape deviation

### Recommended Hardware

For prototype development:

* Raspberry Pi Camera Module
* USB industrial camera
* Global shutter camera, if the tile moves during capture
* Fixed focal length lens
* Diffused LED lighting
* Backlight panel for outline and shape detection

For industrial development:

* Industrial machine vision camera
* Global shutter sensor
* C-mount lens
* LED dome light or bar light
* Triggered image capture using photoelectric sensor
* Rugged camera housing

### Camera Placement

The most basic setup uses one top-facing camera. A more advanced system may use multiple cameras:

* Top camera for surface inspection and tile outline
* Side camera for thickness or side-edge defects
* Corner-focused cameras for broken corners
* Backlight camera for precise contour detection

### Lighting Requirements

Lighting is critical for reliable image processing. The camera station should use:

* Constant LED lighting
* Diffused lighting to reduce reflections
* Enclosed inspection chamber to block external light
* Backlighting if shape and broken edge detection are important
* Calibration target for camera alignment

### Image Processing Pipeline

```text
Image Capture
   |
   v
Lens Distortion Correction
   |
   v
Lighting Normalization
   |
   v
Tile Segmentation
   |
   v
Edge and Corner Detection
   |
   v
Surface Defect Detection
   |
   v
Crack / Chip / Break Classification
   |
   v
Visual Quality Score
```

### Possible Algorithms

For prototype:

* Thresholding
* Edge detection
* Contour detection
* Hough line detection
* Corner detection
* Basic crack segmentation
* OpenCV-based image processing

For advanced versions:

* Convolutional neural network classification
* Object detection model for cracks and chips
* Semantic segmentation for defect area measurement
* Anomaly detection model trained on good tiles

### Visual Inspection Output

The camera module should output:

* Tile detected: yes/no
* Tile orientation
* Tile outline dimensions in image coordinates
* Crack detected: yes/no
* Broken corner detected: yes/no
* Chip detected: yes/no
* Defect area percentage
* Visual grade score
* Image file path or image ID
* Running tile count (this station is first in line, so it owns the master tile count — see Role as First Station above)
* Estimated belt speed (secondary/cross-check value only, derived from frame-to-frame tile motion — not the tracking source of truth; see Role as First Station above)

---

## 6.2 Acoustic Inspection System

### Purpose

The acoustic inspection station identifies hidden cracks or internal defects by analyzing the sound produced when the tile is tapped. A good tile usually produces a clearer ringing response, while a cracked or defective tile may produce a duller, shorter, or irregular sound.

### Core Idea

The system creates a controlled impact on the tile and records the resulting sound. The sound is converted into digital audio data. Noise is removed, and frequency analysis is performed to extract features such as dominant frequency, resonance peaks, decay rate, energy distribution, and spectral irregularities.

### Controlled Impact Requirement

The tapping action must be:

* Constant
* Precise
* Repeatable
* Mechanically stable
* Located at the same point on every tile
* Applied with similar force every time
* Isolated from conveyor vibration as much as possible

### Decision (2026-07-10): Ball-Drop Impactor

The chosen impact mechanism for the current build is a **ball-drop impactor**, not the
push-pull solenoid striker described below (kept below for background/rationale — the
repeatability requirements above still apply regardless of which mechanism is used).

Sequence: a laser or ToF sensor detects the tile has arrived at the station → an Arduino
UNO Q triggers a ball release → the ball falls under gravity and strikes the tile → the
same UNO Q captures the resulting sound and does the required processing (filtering,
FFT, feature extraction) → the acoustic grade/result is sent to the master.

### Release Mechanism (Decision, 2026-08-20): Dual-Solenoid Arm + Lock

The release mechanism is now decided: **two solenoids, ARM and LOCK, driving the
gravity-drop**. Neither solenoid strikes the tile directly.

* **ARM solenoid** — energizes briefly to raise the striker ball up to drop height into
  the LOCK latch's catch, then de-energizes and retracts out of the way.
* **LOCK solenoid** — normally de-energized = latch closed, holding the ball at drop
  height. Energizing it briefly opens the latch, releasing the ball to free-fall under
  gravity onto the tile — this is the actual impact.

Full pin assignments, MOSFET driver wiring, timing sequence, and BOM are in
`documents/electrical/schematics/acoustic_station_wiring.md`. State-machine
implementation: `acoustic_node/sketch/sketch.ino` (MCU, real-time) and
`acoustic_node/python/acoustic/tap_sequencer.py` (pure Python mirror + dev-machine
simulator).

Still open / not yet decided:

* Ball material, mass, and drop height — these fix the impact energy (`E = mgh`) and
  need to be chosen and calibrated together, not independently (blocked on SMTW tile
  size/weight data, see requirements.md Open Items).
* Reload/reset mechanism between tiles — the ball needs a return path back to the ARM
  lift platform's rest position after each strike; not designed yet.
* Mechanical lift/latch hardware itself — the wiring doc's mechanism diagram is a
  conceptual placeholder pending real CAD.

This changes the tapping actuator described in §8.4 (Actuators), and touches every other
place in this charter that assumes a solenoid directly striking the tile (§17 Safety,
§19 Prototype Build Plan, §20 BOM, §25 Conclusion). Those sections have not been rewritten
yet — treat "solenoid hammer" elsewhere in this document as the background/alternative
mechanism until the ball-drop design is finalized and those sections are reconciled (see
`TODO.md`).

### Recommended Tapping Mechanism (background)

The preferred prototype solution is a **push-pull solenoid impact mechanism**.

A push-pull solenoid can move a small striker forward and backward in a repeatable way. When energized, it drives the striker into the tile. When de-energized, a spring or reverse action retracts it.

### Why a Push-Pull Solenoid Is Suitable

* Simple mechanism
* Fast actuation
* Low cost
* Easy electronic control
* Repeatable movement
* Compact size
* Easy to trigger from a microcontroller
* Can be mounted above or beside the tile

### Solenoid Hammer Design

```text
Solenoid Body
   |
   v
Moving Plunger / Shaft
   |
   v
Replaceable Striker Tip
   |
   v
Tile Surface
```

### Striker Tip Considerations

The hammer tip material affects the sound. Options include:

* Steel tip: sharper impact, stronger high-frequency content
* Nylon tip: softer impact, less risk of surface damage
* Rubber tip: safer but may reduce ringing response
* Hardened plastic tip: balanced option

For tile inspection, the tip should produce enough acoustic energy without damaging the tile surface. A replaceable nylon or hardened plastic striker may be safest for early testing.

### Impact Position

The impact should be applied at a consistent location, such as:

* Near the center of the tile
* A fixed offset from one corner
* One or more test points depending on tile size

For prototype testing, start with one central tap. Later, test multiple tap positions to improve defect detection accuracy.

### Impact Force Control

To improve repeatability:

* Use a fixed solenoid stroke length.
* Use a regulated power supply.
* Keep solenoid voltage constant.
* Use a mechanical stop to define impact depth.
* Use spring return for consistent reset.
* Add a force sensor in advanced versions if needed.
* Use the same striker mass and tip material throughout testing.

### Acoustic Capture Hardware

The microphone should capture the tile sound clearly while minimizing surrounding noise.

Possible microphone options:

1. **USB measurement microphone**, such as a calibrated measurement microphone.
2. **USB condenser microphone**, such as a budget desktop USB mic.
3. **MEMS microphone module**, connected to a microcontroller or single-board computer.
4. **Contact piezo sensor**, attached to a fixture or tile support.
5. **Industrial acoustic sensor**, for rugged final production use.

### Microphone Selection

For early prototyping, a USB microphone can be used because it is simple to connect to a Raspberry Pi or computer. A measurement microphone is preferred over a general USB condenser microphone because it usually has a flatter frequency response and is better suited for frequency analysis.

A budget USB condenser microphone may work for initial experiments, but it may not be ideal for accurate measurement because its response may be shaped for voice recording rather than engineering analysis.

### Microphone Placement

Recommended microphone placement:

* 5 cm to 20 cm from impact point during prototype tests
* Fixed mount, not handheld
* Isolated from machine vibration
* Protected from dust
* Directed toward the tile surface
* Enclosed inside an acoustic chamber if possible

### Acoustic Chamber

A small enclosure around the tapping and microphone area is recommended to reduce factory noise.

The chamber may include:

* Foam or acoustic damping material
* Rigid frame
* Door or slot for tile movement
* Internal microphone mount
* Solenoid mount
* Vibration-isolated tile support

### Audio Processing Pipeline

```text
Trigger Tap
   |
   v
Record Audio Window
   |
   v
Remove Background Noise
   |
   v
Apply Bandpass Filter
   |
   v
Normalize Signal
   |
   v
Calculate FFT / Frequency Spectrum
   |
   v
Extract Acoustic Features
   |
   v
Compare With Good Tile Reference
   |
   v
Acoustic Quality Score
```

### Audio Features to Extract

The system should extract features such as:

* Dominant frequency
* Top resonance peaks
* Peak amplitude
* Spectral centroid
* Band energy ratios
* Ring decay time
* Damping factor
* RMS energy
* Zero crossing rate
* Mel-frequency features, if using machine learning
* Spectral flatness
* Difference from reference good-tile signature

### Noise Removal

Noise can be reduced using:

* Recording a background noise sample before the tap
* Subtracting or estimating noise profile
* Bandpass filtering
* Windowing the impact response
* Discarding pre-impact audio
* Using an acoustic enclosure
* Using vibration isolation
* Averaging multiple taps if production speed allows

### Example Acoustic Test Sequence

```text
1. Tile reaches acoustic station.
2. Conveyor stops or slows.
3. Clamp or support holds tile steady.
4. Microphone starts recording.
5. Solenoid tapper is triggered.
6. Audio is recorded for a short window.
7. Audio is filtered and processed.
8. FFT is calculated.
9. Acoustic features are extracted.
10. Defect probability is calculated.
11. Tile is released to sorting station.
```

### Expected Frequency Considerations

Ceramic and terracotta tiles can produce resonant sounds across a broad audible frequency range. The exact frequency depends on tile material, size, thickness, firing quality, crack condition, support method, impact point, and striker material.

Because the exact useful frequency band must be determined experimentally, the system should initially record a wide audible range and then analyze which frequency bands separate good and defective tiles most clearly.

A microphone with a typical 20 Hz to 20 kHz capture range is generally suitable for initial experiments. The key requirement is not only frequency range but also stable mounting, low noise, repeatability, and a reasonably flat response.

---

## 6.3 Dimensional Inspection System

### Purpose

Dimensional inspection ensures that the tile meets size and geometry requirements.

This check matters specifically because ceramic/terracotta tiles shrink and expand
unevenly during kiln firing — the corners of a tile typically deviate from nominal size
more than the center does. A tile can pass a single center-point or overall-outline
measurement and still be out of tolerance at the corners. Dimensional inspection must
therefore sample multiple points across the tile (at minimum: all four corners plus
center), not just one reference point, to catch this.

### Parameters Measured

* Length
* Width
* Thickness
* Squareness
* Edge straightness
* Warpage / bowing
* Surface height variation
* Corner geometry
* Corner-to-center dimensional deviation (kiln shrinkage/expansion check — see Purpose above)

### Sensor Options

#### Time-of-Flight Sensors

Time-of-flight sensors measure distance by calculating the time light takes to travel to the object and return. They are useful for non-contact measurement but may have limited accuracy depending on sensor quality, surface reflectivity, and measurement distance.

Suitable for:

* Presence detection
* Basic height measurement
* Basic dimensional checks
* Prototype flatness measurement

#### Laser Displacement Sensors

Laser displacement sensors are more accurate than basic ToF sensors and are better suited for production-quality dimensional inspection.

Suitable for:

* Thickness measurement
* Height profile
* Flatness measurement
* Edge measurement

#### Line Scan or Area Scan Camera

A camera with calibrated optics can also measure tile outline and dimensions.

Suitable for:

* Length and width
* Shape
* Corner breakage
* Edge geometry

### Recommended Prototype Approach

For the prototype:

* Use camera-based outline measurement for length, width, and shape.
* Use ToF sensors for height/thickness reference checks.
* Use multiple fixed ToF sensors across the tile width to estimate warpage.

**Open option — camera-only dimensional measurement:** since the visual inspection
station already extracts the tile contour (see `Camera_Sorting_Subsystem.md`), the same
calibrated image may be usable to derive length/width/corner-vs-center deviation without
separate ToF/laser hardware, using the camera as the sole dimensional sensor. Thickness
and true flatness/warpage still need a distance sensor (a single top-down camera can't
measure height). Not yet decided — evaluate once real tile samples and a calibrated
camera rig are available; if adopted, this removes ToF/laser hardware from the BOM for
length/width/shape but not for thickness/flatness.

### Dimensional Processing Pipeline

```text
Sensor Trigger
   |
   v
Read Distance Values
   |
   v
Filter Outliers
   |
   v
Convert to Physical Dimensions
   |
   v
Compare Against Nominal Size
   |
   v
Calculate Dimensional Score
```

### Dimensional Output

The dimensional module should output:

* Measured length
* Measured width
* Measured thickness
* Flatness deviation
* Squareness deviation
* Pass/fail against tolerance
* Dimensional grade score

---

## 7. Mechanical Architecture

## 7.1 Conveyor System

The conveyor transports tiles through each station.

### Requirements

* Stable tile movement
* Low vibration
* Adjustable speed
* Enough load capacity for tile weight
* Position repeatability
* Easy cleaning
* Suitable surface material to avoid scratching tiles

### Conveyor Options

* Belt conveyor
* Roller conveyor
* Timing belt conveyor
* Chain conveyor with fixtures
* Indexing conveyor

For accurate inspection, an **indexing conveyor** is preferred. It moves the tile to a station, stops, performs inspection, and then moves the tile to the next station.

### Conveyor Control

The conveyor may be controlled using:

* Stepper motor
* Servo motor
* AC motor with VFD
* DC gear motor with encoder

For a prototype, a stepper motor or DC motor with encoder can be used. For production, a servo-based indexing system is more reliable.

---

## 7.2 Tile Alignment Mechanism

Accurate tile alignment improves inspection repeatability.

### Possible Alignment Methods

* Side rails
* Mechanical guides
* Pneumatic pushers
* Servo-driven centering arms
* Spring-loaded rollers
* End stop with side reference edge

### Recommended Approach

For the prototype:

* Use fixed side guides.
* Use an end stop at each inspection station.
* Use a simple pneumatic or solenoid pusher to square the tile against a reference edge.

For production:

* Use servo-controlled alignment arms.
* Add sensors to confirm tile position.
* Use automatic correction for different tile sizes.

---

## 7.3 Acoustic Tapping Station

### Mechanical Requirements

* Rigid solenoid mount
* Adjustable striker height
* Replaceable striker tip
* Mechanical stop for impact consistency
* Vibration isolation
* Tile support underneath impact point
* Microphone mounting bracket
* Enclosed noise-reduction chamber

### Tile Support During Tapping

Tile support strongly affects sound. The support must be consistent for every tile.

Options:

* Soft rubber support pads at fixed positions
* Three-point support fixture
* Edge support frame
* Conveyor belt support with local hard backing
* Lift-and-support platform under the tile

For acoustic consistency, a **three-point or four-point support fixture** is recommended. The tile should contact the same support points every time.

---

## 7.4 Sorting Mechanism

After inspection, each tile must be routed based on grade.

### Sorting Options

#### Pneumatic Pusher

A pneumatic pusher pushes the tile into a side lane.

Advantages:

* Simple
* Fast
* Low cost
* Easy to control

Limitations:

* May damage fragile tiles if force is too high
* Requires compressed air
* Less gentle than robotic handling

#### Servo Diverter

A servo-controlled gate diverts the tile into different paths.

Advantages:

* Controlled movement
* Adjustable
* Good for conveyor-based sorting

Limitations:

* Requires careful mechanical design

#### Pick-and-Place Robot

A robot picks the tile and places it in the correct stack.

Advantages:

* Flexible
* Gentle handling possible
* Useful for packing

Limitations:

* Higher cost
* More complex control

#### Delta Robot / Cartesian Gantry

A gantry or delta robot can move tiles into different stacks.

Advantages:

* Suitable for repetitive sorting
* Can support stacking and packing

Limitations:

* Requires vacuum gripper design
* More expensive than simple diverters

### Decision (2026-07-10)

The chosen handling mechanism is a **Cartesian gantry performing pick-and-place**, under
machine control, rather than pneumatic pushers or servo diverters. The gantry receives
the final grade + position from the master (see `Automation_Architecture.md` §2, §5) and
places each tile into the correct grade lane/stack/packing station directly. The options
below are retained for background/rationale; the gantry is the confirmed direction for
both prototype and production, not just a production-phase upgrade.

**Control architecture decision:** the gantry is driven by a custom machine-control
layer, not an off-the-shelf CNC controller/G-code interpreter (e.g. GRBL, Mach3,
LinuxCNC). The master/decision layer issues high-level, semantic commands (e.g. "place
current tile at Grade-A stack" — a slot/lane identifier and grade, not raw coordinates),
and a lower-level machine-control layer translates that into the actual axis motion
(coordinates, motion profile, gripper sequencing). This mirrors the existing high-level
processing → microcontroller/PLC real-time control split in §8.2, applied specifically
to the gantry. Rationale: a semantic command interface decouples the master's sorting
logic from the gantry's physical layout/kinematics, and avoids depending on a general-purpose
CNC toolchain built for machining rather than pick-and-place sorting.

### Recommended Prototype Sorting

For the prototype:

* Use conveyor lanes and pneumatic or servo diverters.
* Start with two categories: Accept and Reject.
* Expand to multiple grades later.

### Recommended Production Sorting

For production:

* Use servo diverters for grade lanes.
* Use robotic pick-and-place or gantry stacking for packing.
* Use vacuum grippers with soft pads for tile handling.

---

## 7.5 Packing System

The packing system should group tiles by grade and count.

### Packing Functions

* Count tiles per grade
* Stack tiles evenly
* Separate rejected tiles
* Prepare batches for wrapping or boxing
* Send daily production data to dashboard

### Possible Packing Architecture

```text
Sorted Output Lane
   |
   v
Counting Sensor
   |
   v
Stacking Platform
   |
   v
Batch Complete Signal
   |
   v
Manual or Automatic Packing
```

### Advanced Packing

In a later phase, the system may include:

* Automatic stacking lift
* Vacuum pick-and-place stacking
* Cardboard box loading
* Shrink wrapping
* Label printing
* Grade label application
* Barcode or QR code generation

---

## 8. Electronics and Control Architecture

## 8.1 Main Computing Unit

The system needs a processing computer for image processing, audio processing, dimensional analysis, decision-making, and data logging.

### Prototype Options

* Raspberry Pi 4 or Raspberry Pi 5
* Mini PC
* Laptop or desktop computer
* NVIDIA Jetson Nano / Orin Nano for AI-based vision

### Recommended Prototype Setup

A Raspberry Pi can be used for early testing, especially for camera capture, USB microphone capture, sensor reading, and basic sorting control. However, if deep learning image analysis is required, a mini PC or NVIDIA Jetson may be more suitable.

### Recommended Final Setup

For an industrial system:

* Industrial PC for image and audio processing
* PLC for real-time machine control
* Microcontroller modules for local sensor/actuator tasks
* HMI screen for operator control

---

## 8.2 Control Layer

The control layer coordinates conveyor movement, sensors, solenoid triggering, sorting actuators, and safety interlocks.

### Control Hardware Options

* Arduino / ESP32 for simple prototype actuation
* Raspberry Pi GPIO for low-level control, with driver circuits
* PLC for industrial machine control
* Motor controller for conveyor
* Relay or MOSFET driver for solenoid
* Pneumatic valve controller for air cylinders

### Recommended Control Division

```text
Industrial PC / Raspberry Pi
   |
   | High-level processing and decision
   v
Microcontroller or PLC
   |
   | Real-time control
   v
Motors, Solenoid, Sensors, Diverters, Safety Interlocks
```

The Raspberry Pi or computer should not directly drive motors or solenoids. It should send commands to motor drivers, relay modules, MOSFET drivers, or PLC output modules.

The pick-and-place gantry follows this same split rather than a CNC/G-code controller —
see §7.4 (Sorting Mechanism, Control architecture decision) for the gantry-specific
command interface (master issues high-level place commands; a dedicated machine-control
layer translates them to axis motion).

---

## 8.3 Sensor Layer

### Sensors Required

* Tile presence sensor at input
* Tile position sensor at each station
* Camera trigger sensor
* Acoustic station presence sensor
* Microphone
* ToF or distance sensors
* Sorting lane confirmation sensor
* Counter sensor at output
* Emergency stop input
* Door/interlock sensor for acoustic chamber

### Suitable Sensor Types

* Photoelectric sensors
* Inductive sensors for machine position
* Limit switches
* Optical encoders
* ToF distance sensors
* Laser displacement sensors
* Load cells, if weight measurement is added

---

## 8.4 Actuators

### Actuators Required

* Conveyor motor
* Solenoid hammer *(background/alternative — current plan is a ball-drop impactor with the release actuator TBD; see §6.2 Decision)*
* Pneumatic alignment pusher
* Sorting diverter or pusher
* Stacking lift, in advanced version
* Vacuum gripper, if robotic packing is used

### Solenoid Driver

The solenoid should be driven using:

* Correct rated DC power supply
* MOSFET or relay driver
* Flyback diode or snubber protection
* Trigger control signal from microcontroller or PLC
* Adjustable pulse duration

### Important Solenoid Control Parameters

* Voltage
* Current
* Pulse duration
* Stroke length
* Cooling time
* Duty cycle
* Impact repeatability

---

## 9. Software Architecture

## 9.1 Software Modules

The software should be modular. Suggested modules:

1. **Machine control module**
2. **Camera acquisition module**
3. **Visual inspection module**
4. **Audio acquisition module**
5. **Acoustic analysis module**
6. **Dimensional sensor module**
7. **Decision engine**
8. **Sorting control module**
9. **Database logging module**
10. **Dashboard / HMI module**
11. **Calibration module**
12. **Reporting module**

---

## 9.2 Data Flow

```text
Tile ID Generated
   |
   +--> Camera Image Captured
   |       |
   |       v
   |   Visual Analysis Result
   |
   +--> Audio Captured
   |       |
   |       v
   |   Acoustic Analysis Result
   |
   +--> Dimensions Captured
           |
           v
       Dimensional Result

All Results Combined
   |
   v
Decision Engine
   |
   v
Grade Assigned
   |
   v
Sorting Command
   |
   v
Database Entry + Dashboard Update
```

---

## 9.3 Tile ID and Tracking

Every tile should receive a unique ID when it enters the system.

Example tile ID format:

```text
YYYYMMDD-BATCH-LINE-SEQUENCE
```

Example:

```text
20260530-B01-L01-000125
```

Each tile ID links all inspection results together.

Camera, acoustic, and dimensional inspection each run on their own compute node,
processing independently and reporting a compact result to the master (see
`Automation_Architecture.md` §2–§5). Because the three stations run asynchronously, the
ID alone isn't enough — the system also needs a robust way to know *which physical tile
on the conveyor* a given station result belongs to at the moment it's produced.
`Automation_Architecture.md` §8 ("Conveyor Tracking") covers this: time-delay tracking is
explicitly ruled out (fails under slip, speed changes, jams, e-stop, robot delay), in
favor of encoder-position-based tracking, where each tile's entry encoder count is
recorded and each station's expected arrival is computed from conveyor position rather
than elapsed time. Treat that section as the authoritative design for this — this section
only defines the ID format, not the tracking mechanism.

---

## 9.4 Database Schema

### Tile Inspection Table

| Field                 | Description                   |
| --------------------- | ----------------------------- |
| tile_id               | Unique tile ID                |
| timestamp             | Inspection time               |
| batch_id              | Production batch              |
| visual_status         | Pass/fail from camera         |
| crack_detected        | Yes/no                        |
| corner_break_detected | Yes/no                        |
| visual_score          | Numerical visual score        |
| acoustic_status       | Pass/fail from sound analysis |
| dominant_frequency    | Main frequency peak           |
| acoustic_score        | Numerical acoustic score      |
| dimension_status      | Pass/fail from measurement    |
| length_mm             | Measured length               |
| width_mm              | Measured width                |
| thickness_mm          | Measured thickness            |
| flatness_mm           | Flatness deviation            |
| final_grade           | A/B/C/Reject                  |
| sorting_lane          | Output lane number            |
| image_path            | Stored image path             |
| audio_path            | Stored audio path             |
| operator_id           | Operator or shift ID          |
| remarks               | Additional notes              |

### Daily Summary Table

| Field               | Description                         |
| ------------------- | ----------------------------------- |
| date                | Production date                     |
| batch_id            | Batch number                        |
| total_tiles         | Total tiles inspected               |
| grade_a_count       | Grade A count                       |
| grade_b_count       | Grade B count                       |
| grade_c_count       | Grade C count                       |
| reject_count        | Reject count                        |
| visual_rejects      | Rejected due to camera inspection   |
| acoustic_rejects    | Rejected due to acoustic inspection |
| dimensional_rejects | Rejected due to dimension           |
| machine_runtime     | Operating time                      |
| downtime            | Downtime                            |

---

## 10. Decision and Grading Logic

## 10.1 Basic Rule-Based Grading

A simple first version can use rule-based decisions.

Example:

```text
If major crack visible = Reject
Else if broken corner above limit = Reject
Else if acoustic score below threshold = Reject
Else if dimension outside tolerance = Reject
Else if minor defect detected = Grade B
Else = Grade A
```

## 10.2 Weighted Score Grading

A more flexible system can calculate a final score.

```text
Final Score = Visual Score × 0.4 + Acoustic Score × 0.4 + Dimensional Score × 0.2
```

Example grading:

| Final Score | Grade   |
| ----------- | ------- |
| 90 to 100   | Grade A |
| 75 to 89    | Grade B |
| 60 to 74    | Grade C |
| Below 60    | Reject  |

## 10.3 Safety-Based Override Rules

Some defects should override the score and directly reject the tile.

Direct rejection conditions:

* Large visible crack
* Broken corner beyond allowed size
* Missing tile section
* Acoustic signature strongly indicates internal crack
* Dimension exceeds maximum tolerance
* Tile not detected properly
* Image or audio capture failed

---

## 11. Acoustic Classification Strategy

## 11.1 Experimental Data Collection

Before finalizing acoustic thresholds, the system must collect data from known good and defective tiles.

Recommended dataset:

* Good tiles from multiple batches
* Tiles with visible cracks
* Tiles with hidden/internal cracks
* Corner-broken tiles
* Different thickness tiles
* Different moisture or firing conditions
* Different tile sizes, if applicable

Each tile should be labeled manually during dataset creation.

## 11.2 Acoustic Feature Comparison

Good and defective tiles should be compared using:

* Dominant frequency shift
* Missing resonance peaks
* Increased damping
* Lower ring duration
* Broader spectral distribution
* Reduced high-frequency energy
* Higher spectral noise

## 11.3 Classification Methods

### Stage 1: Threshold Method

Use simple thresholds:

* Dominant frequency within acceptable range
* Ring duration above minimum value
* Spectral energy ratio within accepted range
* Acoustic score above threshold

### Stage 2: Machine Learning Method

Train a classifier using extracted features:

* Logistic regression
* Random forest
* Support vector machine
* Gradient boosting
* Neural network

### Stage 3: Audio Deep Learning

Use spectrograms as input to a model:

* Convert audio to spectrogram
* Train CNN model to classify good vs cracked
* Use dataset augmentation if needed

---

## 12. Hardware Recommendation by Prototype Phase

## 12.1 Phase 1: Laboratory Proof of Concept

### Goal

Prove that camera, acoustic, and dimensional data can separate good and bad tiles.

### Hardware

* Raspberry Pi or laptop
* USB microphone
* Push-pull solenoid
* MOSFET solenoid driver
* DC power supply
* Basic camera
* LED lighting
* Manual tile placement jig
* Basic ToF distance sensor
* Python processing software

### Output

* Saved image per tile
* Saved audio per tap
* FFT plot
* Basic dimension readings
* Manual classification comparison

---

## 12.2 Phase 2: Semi-Automated Prototype

### Goal

Build a small working line with controlled movement and automatic inspection.

### Hardware

* Small belt conveyor
* Stepper motor or DC motor with encoder
* Raspberry Pi / mini PC
* Camera with fixed lighting
* Solenoid hammer station
* USB measurement microphone
* Multiple ToF sensors
* Photoelectric sensors
* Pneumatic or servo pusher
* Simple output lanes
* Database logging

### Output

* Tile count
* Grade decision
* Automatic reject sorting
* Daily report
* Dashboard interface

---

## 12.3 Phase 3: Industrial Prototype

### Goal

Create a robust version suitable for factory testing.

### Hardware

* Industrial conveyor
* PLC for real-time control
* Industrial PC for processing
* Industrial machine vision camera
* Measurement-grade microphone or acoustic sensor
* Laser displacement sensors
* Servo-controlled sorting gates
* Acoustic enclosure
* Safety guarding
* HMI screen
* Database server or local storage

### Output

* Multi-grade sorting
* Better measurement repeatability
* Production-quality logging
* Operator control panel
* Integration with packing line

---

## 13. Raspberry Pi Role in the Project

The Raspberry Pi can be used as a prototype computing platform.

### Raspberry Pi Tasks

* Capture images from a camera
* Record audio from a USB microphone
* Read ToF sensors
* Run Python scripts
* Calculate FFT and acoustic features
* Perform OpenCV-based image processing
* Store data locally
* Communicate with microcontroller or PLC
* Display a simple dashboard

### Raspberry Pi Limitations

* Limited processing power for heavy AI vision models
* Not ideal for precise real-time motor control
* GPIO should not directly drive high-current devices
* Industrial reliability may be limited
* Requires protection from dust, vibration, and electrical noise

### Recommended Use

Use Raspberry Pi for early development and testing. For final production, move real-time control to a PLC and heavy processing to an industrial PC or edge AI computer.

---

## 14. Data Logging and Dashboard

### Monitoring Architecture (Decision, 2026-07-10)

Monitoring is two-tier, not a single dashboard:

* **Station-local monitor** — each subsystem (camera, acoustic, dimensional) shall have
  its own display showing what's happening at that station in real time: e.g. the
  camera station shows the live image + detection overlay, the acoustic station shows
  the live waveform/FFT, the dimensional station shows live readings. This runs on or
  next to the station's own compute node.
* **Master/overall monitor** — a system-wide dashboard on the master showing the full
  line's status (§14.1 below already specifies this content).

Both views are built from the same per-tile data the master already collects — the
station-local view is just that station's own slice, shown locally rather than only
routed through the master.

**Industrial reuse of the data:** the production data this system logs (tile counts per
grade, defect type breakdown, rejection reasons, throughput) is intended to be useful
beyond this project — i.e. it should be structured as real production/quality data the
tile works could act on (matching §14.2 Reports), not just prototype debugging output.

## 14.1 Dashboard Features

The dashboard should show:

* Total tiles inspected today
* Grade A count
* Grade B count
* Grade C count
* Reject count
* Rejection reason breakdown
* Current tile result
* Camera image preview
* Acoustic waveform and FFT preview
* Dimension readings
* Machine status
* Conveyor status
* Error alarms
* Batch ID
* Shift ID

## 14.2 Reports

Reports should include:

* Daily production count
* Grade distribution
* Reject percentage
* Common defect types
* Acoustic defect count
* Visual defect count
* Dimensional defect count
* Machine uptime and downtime
* Batch comparison

## 14.3 Storage

Recommended storage methods:

* SQLite for prototype
* PostgreSQL or MySQL for production
* Local image/audio file storage
* Periodic backup to server
* CSV export for analysis

---

## 15. Calibration Plan

## 15.1 Camera Calibration

* Calibrate lens distortion.
* Set fixed camera height.
* Use calibration grid.
* Calibrate pixel-to-mm conversion.
* Lock exposure and white balance.
* Use fixed lighting.

## 15.2 Acoustic Calibration

* Use same striker tip and impact force.
* Use same microphone distance.
* Record background noise level.
* Test known good tiles.
* Test known defective tiles.
* Build reference acoustic profiles.
* Repeat calibration after changing tile type or hammer tip.

## 15.3 Dimensional Calibration

* Use reference gauge block or known tile sample.
* Calibrate ToF or laser sensor offsets.
* Verify measurement repeatability.
* Record sensor drift.
* Recalibrate periodically.

## 15.4 Sorting Calibration

* Confirm diverter timing.
* Confirm tile reaches correct lane.
* Adjust conveyor speed and actuator delay.
* Test at low speed first.
* Increase speed gradually.

---

## 16. Machine Operation Sequence

### Step-by-Step Sequence

```text
1. Operator starts system.
2. System performs self-check.
3. Conveyor starts.
4. Tile enters input station.
5. Presence sensor detects tile.
6. Tile ID is assigned.
7. Tile is aligned mechanically.
8. Camera captures tile image.
9. Dimension sensors measure tile geometry.
10. Tile moves to acoustic station.
11. Conveyor stops or indexes tile into position.
12. Solenoid hammer taps tile.
13. Microphone records sound.
14. Audio is processed.
15. Camera, acoustic, and dimensional results are combined.
16. Final grade is assigned.
17. Sorting mechanism routes tile to correct lane.
18. Tile count and result are saved.
19. Dashboard updates.
20. System repeats for next tile.
```

---

## 17. Safety Requirements

### Mechanical Safety

* Emergency stop button
* Guarding around moving parts
* Guarding around solenoid hammer
* No exposed pinch points
* Safe conveyor access
* Lockout procedure for maintenance

### Electrical Safety

* Proper grounding
* Fuses or circuit breakers
* Isolated power supplies
* Protected solenoid driver circuit
* Cable management
* Enclosure for electronics

### Operational Safety

* Safety interlock on acoustic chamber
* Alarm on sensor failure
* Stop machine if tile jam detected
* Stop if sorting actuator fails
* Overcurrent protection for motors and solenoids

---

## 18. Risks and Challenges

## 18.1 Acoustic Variability

Tile sound can change due to material type, thickness, support condition, moisture, and impact point.

Mitigation:

* Use fixed support fixture.
* Use repeatable tapping mechanism.
* Collect a large dataset.
* Calibrate per tile type.
* Use multiple acoustic features instead of one frequency only.

## 18.2 Factory Noise

Factory noise can affect microphone readings.

Mitigation:

* Use acoustic enclosure.
* Use close microphone placement.
* Use bandpass filtering.
* Use background noise subtraction.
* Consider contact sensor or piezo sensor.

## 18.3 Camera Lighting Variation

Changing light affects image processing.

Mitigation:

* Enclose camera station.
* Use fixed LED lighting.
* Lock camera exposure.
* Use calibration images.

## 18.4 Tile Damage During Handling

Sorting or tapping could damage tiles.

Mitigation:

* Use soft guides.
* Control actuator force.
* Use soft striker tip.
* Use vacuum grippers with rubber pads.
* Test impact force carefully.

## 18.5 Measurement Repeatability

Inconsistent positioning causes bad data.

Mitigation:

* Use alignment stops.
* Use indexing conveyor.
* Use mechanical references.
* Add position confirmation sensors.

---

## 19. Prototype Build Plan

## Phase 1: Manual Test Bench

### Build

* Fixed tile support frame
* Solenoid tapper mounted above tile
* USB microphone mounted near tile
* Camera mounted above tile
* Basic ToF sensor fixture
* Raspberry Pi or laptop for data capture

### Test

* Tap good and defective tiles
* Record audio
* Plot waveform and FFT
* Capture images
* Test crack detection
* Log dimension values

### Success Criteria

* Repeatable sound capture
* Visible difference between good and cracked tile acoustic signatures
* Camera can detect major cracks and broken corners
* Sensor readings are stable enough for basic dimensional comparison

---

## Phase 2: Conveyor Prototype

### Build

* Small conveyor
* Tile presence sensors
* Alignment guide
* Camera inspection station
* Acoustic tapping station
* Output reject pusher
* Raspberry Pi or mini PC dashboard

### Test

* Run tiles one by one
* Automatically trigger camera and microphone
* Automatically grade tile
* Automatically reject bad tile
* Log every tile

### Success Criteria

* Automatic tile counting
* Stable camera images
* Repeatable acoustic impact
* Correct reject sorting
* Daily log generated

---

## Phase 3: Multi-Grade Sorting

### Build

* Multiple sorting lanes
* Grade A, B, C, Reject outputs
* Improved dimensional sensors
* Better acoustic enclosure
* Database and dashboard upgrade

### Test

* Run batch of tiles
* Compare machine grades with human inspector grades
* Tune thresholds
* Measure false accept and false reject rates

### Success Criteria

* Reliable multi-grade classification
* Acceptable sorting accuracy
* Reduced manual inspection workload

---

## Phase 4: Packing Integration

### Build

* Counting system per grade
* Stacking platform
* Manual or automatic packing station
* Batch label generation

### Test

* Count sorted tiles by grade
* Prepare stack for packing
* Generate report and labels

### Success Criteria

* Sorted tiles are counted correctly
* Packing batches are traceable
* Operator can see batch summary

---

## 20. Suggested Bill of Materials for Prototype

| Category          | Item                              | Purpose                       |
| ----------------- | --------------------------------- | ----------------------------- |
| Computing         | Raspberry Pi 4/5 or mini PC       | Processing and control        |
| Camera            | USB camera or Raspberry Pi camera | Visual inspection             |
| Lighting          | LED light panel or ring light     | Stable image capture          |
| Acoustic actuator | Push-pull solenoid                | Repeatable tile tapping       |
| Driver            | MOSFET driver module              | Solenoid switching            |
| Power             | DC power supply                   | Solenoid and electronics      |
| Audio             | USB measurement microphone        | Acoustic capture              |
| Sensors           | ToF sensors                       | Distance/dimensional readings |
| Presence          | Photoelectric sensors             | Tile detection                |
| Motor             | Stepper or DC geared motor        | Conveyor movement             |
| Actuator          | Servo or pneumatic pusher         | Sorting                       |
| Frame             | Aluminum extrusion                | Machine structure             |
| Conveyor          | Belt conveyor                     | Tile movement                 |
| Safety            | Emergency stop switch             | Safety shutdown               |
| Software          | Python, OpenCV, NumPy, SciPy      | Processing                    |
| Database          | SQLite                            | Prototype data logging        |

---

## 21. Recommended Software Stack

### Prototype

* Python
* OpenCV for image processing
* NumPy for numerical processing
* SciPy for audio filtering and FFT
* PyAudio or sounddevice for audio capture
* SQLite for database
* Flask or Streamlit for dashboard
* GPIO library for Raspberry Pi hardware control

### Production

* PLC ladder logic or structured text for machine control
* Industrial PC software for vision and audio processing
* SQL database
* HMI software
* OPC UA, Modbus TCP, or MQTT for communication
* Dockerized services if using an edge computer

---

## 22. Testing and Validation Plan

## 22.1 Dataset Creation

Create labeled tile samples:

* Good tiles
* Visible cracked tiles
* Hidden cracked tiles
* Broken corner tiles
* Dimensionally incorrect tiles
* Warped tiles

For each tile:

* Assign tile ID
* Take image
* Record audio
* Measure dimensions
* Label actual condition manually

## 22.2 Accuracy Metrics

Track:

* Visual detection accuracy
* Acoustic defect detection accuracy
* Dimensional measurement error
* Final grading accuracy
* False accept rate
* False reject rate
* Sorting accuracy
* Tiles per minute
* System uptime

## 22.3 Repeatability Test

Test the same tile multiple times.

Measure variation in:

* Acoustic dominant frequency
* Ring duration
* Visual score
* Dimension values
* Final grade

## 22.4 Production Simulation

Run mixed batches of good and defective tiles and compare machine output with human inspection.

---

## 23. Future Improvements

Possible upgrades include:

* AI model for crack and chip detection
* Spectrogram-based acoustic neural network
* Laser profile scanning for flatness
* Automatic tile size selection
* Automatic packing robot
* Barcode or QR code tracking
* Cloud dashboard
* Predictive maintenance
* Automatic calibration routine
* Integration with factory ERP system
* Digital twin of production line

---

## 24. Final Proposed System Configuration

For the best balance between cost, reliability, and development speed, the recommended system path is:

### Prototype Configuration

* Raspberry Pi or mini PC
* USB camera with fixed LED lighting
* Push-pull solenoid tapper
* Measurement-type USB microphone
* ToF sensors for basic dimensional checks
* Belt conveyor with position sensors
* Pneumatic or servo pusher for reject sorting
* Python-based software
* SQLite database
* Simple web dashboard

### Industrial Configuration

* Industrial PC for image/audio processing
* PLC for conveyor, actuator, and safety control
* Industrial camera with controlled lighting
* Solenoid or servo-controlled tapping mechanism
* Measurement microphone or industrial acoustic sensor
* Laser displacement sensors for dimensions
* Servo diverters or robotic pick-and-place
* Multi-lane sorting
* Automatic counting and packing support
* SQL database and HMI dashboard

---

## 25. Conclusion

This project proposes a complete automated tile sorting and packing system using visual, acoustic, and dimensional inspection. The system is especially useful because it does not rely on only one inspection method. The camera detects visible defects, the acoustic module identifies hidden structural problems, and the dimensional module verifies physical accuracy.

A controlled push-pull solenoid hammer provides repeatable impact for acoustic testing. A microphone records the sound, and the system analyzes the frequency response to classify tile quality. A camera detects visible cracks and shape damage, while ToF or laser sensors check the tile dimensions. The final decision engine combines all results and sends the tile to the correct sorting or packing path.

The project can be developed step by step, starting from a manual laboratory test bench and progressing toward a full industrial sorting and packing machine. The final system can reduce manual inspection workload, improve sorting consistency, produce useful production records, and support automatic packing after grading.
