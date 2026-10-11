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

Status: built and unit-tested on the dev laptop (16 tests), page rendered against seeded
data; **never connected to a real broker**. The page is unauthenticated; the commands
change how the line behaves, so they stay disabled unless you ask for them, and the
network is assumed isolated (`documents/electrical/network_plan.md`).
