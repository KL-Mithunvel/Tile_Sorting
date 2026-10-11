"""Station fusion: put the camera's result and the tap-test's result for one
tile together, decide its final grade, and hand it to the pick-and-place node.

Everything in here is hardware-independent and runs without a microphone, a
camera or a broker (see CLAUDE.md Development Rule 1):

- `grades`      the one grade vocabulary and its ordering
- `ledger`      which tile does the next tap belong to (FIFO, fail-closed)
- `final_grade` camera grade + sound grade -> final grade + bin
- `store`       SQLite log of every tile (the station's own data record)
- `transport`   topic names, an in-memory bus for tests, an MQTT bus
- `coordinator` glue: bus <-> ledger <-> store, acknowledgements, alarms

Message contract: documents/programming/station_coordination.md. The camera
node does NOT import this package (a separate node on the same Pi); it speaks
the same JSON contract from camera/publisher.py, and
tests/test_station_contract.py keeps the two in step.
"""
