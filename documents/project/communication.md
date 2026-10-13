# Communication — who talks to whom, over what

Status (2026-10-12): an **index and summary** of what is built, written from the
documents below. It does not replace them: when this page and a source disagree, the
source wins.

| Source of truth | Covers |
|---|---|
| `documents/programming/station_coordination.md` | the MQTT topic/payload contract, fail-closed rules, bring-up scripts |
| `documents/programming/nano_serial_protocol.md` | Pi <-> Nano serial line protocol |
| `documents/programming/pick_place_control_protocol.md` | UNO Q Linux <-> gantry MCU line protocol |
| `documents/electrical/network_plan.md` | the control LAN, addresses, time |
| `documents/electrical/i2c_bus_plan.md` | I2C buses (encoders, ToF) |

## 1. The links

```text
                    isolated control LAN 192.168.1.0/24 (D-Link DIR-825, no uplink)
                                   |
   +-------------------------------+-----------------------------+
   |                               |                             |
 Raspberry Pi 5 .11            UNO Q .15                    master PC (.10 planned)
   tile-camera      :5000        pick & place                 observer / collector /
   tile-acoustic    :5001        (gantry MCU on its            commander   :8000
   mosquitto        :1883         own serial link)             -- OPTIONAL, line runs without it
   |
   | USB serial 115200 8N1 (not on the LAN)
 Arduino Nano  (striker + belt PWM, ToF 1 / ToF 3)
```

| Link | Transport | Direction | Notes |
|---|---|---|---|
| camera -> station | MQTT, broker on the Pi | one way | `tile/<id>/camera/result`, heartbeat `station/camera/status` |
| station -> UNO Q | MQTT | request + ack | `tile/<id>/final`, acked by `tile/<id>/ack`; stop-and-wait with retries, in order |
| UNO Q -> station / master | MQTT | one way | `tile/<id>/placed`, retained `station/gantry/status` |
| Nano <-> Pi | USB serial, ASCII, XOR checksum per line | both | Pi sends PING every 500 ms; Nano stops the belt itself after 2000 ms of silence |
| Linux <-> gantry MCU | serial / Bridge, ASCII line protocol | both | Linux owns the coordinate model; MCU owns step generation and safety |
| master <-> station | MQTT | both | observe everything; `station/cmd`, `station/gantry/cmd` for a few commands |
| operator <-> station | HTTP, Flask | browser | Run view `/` (read-only kiosk), Service view `/service` (controls gated by config) |
| laptop dev -> Pi | SSH / `tools/pi/*.bat` | push + run | deploy and bring-up, not part of the line |

## 2. Design rules that hold across every link

1. **The broker lives on the Pi**, so the line runs with no master PC and no uplink.
2. **Order and tile id decide, not clocks.** Tiles are paired with taps by FIFO order and
   identified by `T<date>-<time>-<n>`; the clocks are only kept roughly in step
   (the Pi serves chrony, `network_plan.md` section 6; the service view warns on skew).
3. **Fail closed.** A missing tap, a missing camera result, a silent node or a dead
   link produces `Hold` (manual bin) or a held line, never a guess. Table in
   `station_coordination.md` section 3.
4. **Safety does not depend on the network.** The belt's safety path is Pi -> serial ->
   Nano watchdog, plus the belt-end ToF and the e-stop on the Nano itself; it deliberately
   does not need MQTT or the router.
5. **Diagnostic pings never create work.** `station/diag/ping` is its own topic; answering
   it cannot become a pick job.
6. **Idempotent delivery.** QoS 1 can repeat a message, so consumers de-duplicate on
   `tile_id` (UNO Q feed) or on `seq` (Nano taps).

## 3. What is not decided

- Whether a whole-line start/stop should mean more than the soft stop in `station/cmd`
  (`station_coordination.md`).
- Auth and TLS: none, accepted only while the LAN has no uplink
  (`network_plan.md` section 7).
- Whether Modbus / OPC UA ever appear (`Automation_Architecture.md` mentions them as
  options for an industrial version); nothing implements them.
- Encoder and limit-switch traffic stays on the UNO Q's own buses; it is not on the LAN.
