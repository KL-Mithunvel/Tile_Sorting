// Acoustic node MCU sketch — STUB, not implemented.
//
// Intended responsibility (project_charter.md §6.2 Decision, requirements.md FR-21):
//   1. Watch a laser/ToF sensor for tile arrival at the acoustic station.
//   2. Trigger the ball-drop release mechanism.
//   3. Signal the Linux/Python side (python/main.py) that the impact happened,
//      so audio capture can be timed relative to it.
//
// Deliberately left unimplemented: the release mechanism (electromagnet vs.
// solenoid gate vs. servo latch), ball mass/drop height, and pin assignments
// are all still open decisions (see TODO.md, requirements.md FR-21 Open Items).
// Do not hardcode pin numbers or timing constants here before those are decided
// — per .CLAUDE/CLAUDE.md Development Rule 2, they belong in config, not source.

void setup() {
}

void loop() {
}
