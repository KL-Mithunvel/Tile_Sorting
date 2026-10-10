# Network Plan — D-Link DIR-825 Control LAN

> Companion: `documents/electrical/i2c_bus_plan.md` (the sensor/encoder buses — separate
> from this network).
>
> **Status: PROPOSED, nothing configured yet** (written 2026-10-10). The router has been
> factory-reset by the owner but not set up; no node is on it; every address below is a
> plan, not an observation. The node list is **provisional** — the team is still revising
> it (see §3). Decisions that are made are marked **Decided**; the rest are **Open**.

Source of truth for the *design* of the network is `documents/project/Automation_Architecture.md`
(§12 communication, §23 prototype layout). This file is the electrical/installation side:
what the router is, how to configure it, who gets which address and port.

---

## 1. Purpose and topology

**Decided (2026-10-10):** a D-Link **DIR-825 AC1200** (dual-band Wi-Fi, gigabit switch) is
the control network between the tile-line nodes. It is an **isolated LAN with no uplink** —
no WAN cable, no internet through it. Internet for installs goes through a *separate*
external Wi-Fi that only the Pi (and the dev laptop) joins.

```text
                    external Wi-Fi (internet, installs only)
                                  ·
                                  ·  (Pi + dev laptop only)
   ┌───────────────────────────── · ──────────────────────────────┐
   │                                                              │
   │   D-Link DIR-825   192.168.1.1/24   (DHCP server, Wi-Fi AP)   │
   │      gigabit LAN ports            2.4 / 5 GHz Wi-Fi          │
   └──┬──────────┬──────────┬───────────────┬──────────────────────┘
      │          │          │               │
  Master PC   Raspberry Pi 5            UNO Q                (spare / future)
  .1.10       .1.11                     .1.15                 .1.12 audio unit
  MQTT broker camera + audio            pick-and-place         .1.13 measurement
  dashboard   (2 systemd services)      encoders, limits
                  │ USB
                  ├── USB mic / audio interface
                  ├── camera (CSI ribbon or USB)
                  └── Arduino Nano  (striker + DC belt PWM) — serial, NOT on the LAN
```

The **Nano and the UNO Q share no hardware or wiring** (owner, 2026-10-10). They coordinate
only through the Pi/master over this network; the Nano reaches the LAN solely through the
Pi's USB serial. The belt-end ToF stops the Nano's belt and the pickup ToF tells the UNO Q
a tile is there, independently (`Automation_Architecture.md` §5.5).

Consequences of "no uplink":

- **No NTP.** Every node's clock drifts independently. See §6.
- **No DNS.** Nodes find each other by fixed IP (or `/etc/hosts` entries), not by name lookup.
- **No internet for the nodes on this LAN.** All `apt`/`pip` installs happen over the
  external Wi-Fi before the node is moved onto the control network.

---

## 2. Router: D-Link stock firmware

**Decided (2026-10-10):** stay on **D-Link stock firmware**. OpenWrt is not needed for a
closed LAN of a handful of nodes and can brick the router; revisit only if stock hits a
real limit (e.g. needs SSH/scripting on the router). Hardware revision: **not yet read** —
it is on the bottom label ("H/W Ver"); only matters if OpenWrt is ever considered.

### 2.1 Setup checklist (do in this order, wired from a PC)

Menu names differ between hardware revisions — adapt if yours differ and note the real
names here afterwards.

- [ ] Wired Ethernet from the PC to a router LAN port (an IP change drops Wi-Fi).
- [ ] `http://192.168.0.1` (factory) → log in `admin` / `admin` (may be blank on some revisions).
- [ ] **Change the admin password immediately** (Tools / Maintenance → Admin). Keep it
      outside the repo. Factory credentials are known to anyone in Wi-Fi range.
- [ ] Setup → Network Settings: Router IP `192.168.1.1`, mask `255.255.255.0`.
      (Chosen to match the existing `192.168.1.x` plan in `Automation_Architecture.md` §23.)
- [ ] DHCP server **on**, pool `192.168.1.100` – `192.168.1.199`. Fixed addresses live in
      `.2` – `.99`, outside the pool, so no phone/laptop can take one.
- [ ] Save → router reboots → renew the PC's lease (replug cable or `ipconfig /renew`) →
      log back in at `http://192.168.1.1`.
- [ ] Wi-Fi: SSID `TileLine` (or team's choice), **WPA2-PSK**, strong passphrase kept
      outside the repo. 2.4 GHz is the safe band for the UNO Q; 5 GHz optional.
- [ ] Disable WPS and remote (WAN-side) administration if the UI exposes them.
- [ ] Add DHCP reservations for every node in §3 (needs each device's MAC).
- [ ] Record the real menu names, firmware version and H/W revision at the bottom of this file.

Fallback: hold the reset button ~10 s with power on → back to `192.168.0.1` / admin / admin.

### 2.2 Fixed addresses — how

**Decided (2026-10-10):** every device has a constant address, done as **DHCP reservations
on the router (MAC → IP)**, not static config on each device. The router is then the one
place the whole address list lives, and a replaced board just needs its MAC swapped.
Devices keep using DHCP. Trade-off: if the router is down, a node can't (re)acquire its
address — acceptable, because the LAN is useless without the router anyway.

---

## 3. Node, address and GUI-port table (PROVISIONAL)

Hostnames/IPs are proposals for the team to edit. MACs are unknown until read off each
device (`ip link` on Linux, router client list, or the board's label).

| Node | Hardware | Hostname | Reserved IP | MAC | Link | Status |
|---|---|---|---|---|---|---|
| Router | D-Link DIR-825 | `tile-router` | `192.168.1.1` | — | — | to configure |
| Master | Owner's PC | `tile-master` | `192.168.1.10` | _TBD_ | Ethernet | PC is master (**Decided**, §5.1) |
| Vision + audio | Raspberry Pi 5 16 GB | `tile-pi` | `192.168.1.11` | _TBD_ | Ethernet | not set up yet |
| Audio (future split) | 2nd Pi / compute unit | `tile-audio` | `192.168.1.12` | _TBD_ | Ethernet | **reserved only** — used if a second unit is bought |
| Measurement | undecided | `tile-measure` | `192.168.1.13` | _TBD_ | Ethernet | node not decided |
| Pick-and-place | Arduino UNO Q | `tile-gantry` | `192.168.1.15` | _TBD — the **hub's** MAC, not the board's_ | **Ethernet via owner's USB-C hub (untested)** | lab board currently named `KLM` |
| Striker + belt | Arduino Nano (ATmega328P) | — | **none** | — | USB serial to `tile-pi` | **not on the LAN** |
| Dev laptop | Owner's laptop | — | pool (`.100+`) | — | Wi-Fi/Ethernet | — |

Notes:

- `.14` was the Mega (conveyor controller) in the §23 layout; the Mega is no longer in the
  design (`Automation_Architecture.md` §5.5), so `.14` is left unused rather than reassigned.
- **Decided (2026-10-10):** the UNO Q goes on the router by **Ethernet through the owner's
  USB-C hub**, which satisfies `Automation_Architecture.md` §12's "wired" preference. The
  board's wired MAC is the hub's, so the DHCP reservation follows the hub (swap the hub and
  the address changes). That the board's Debian recognises the hub, and that it still
  powers the board properly, is **untested** (see TODO).
- If the Pi's camera and audio services are later split onto two units, `tile-audio` takes
  `.12` and nothing else in this table changes.

### 3.1 Flask GUI ports

**Decided (2026-10-10):** every Flask GUI listens on `0.0.0.0` and uses one fixed port, so
any device on the LAN can open `http://<ip>:<port>/`. (`0.0.0.0` is already what the
camera and pick-and-place configs ship with.) There is no auth on these pages — fine on an
isolated LAN, **not** fine if the router ever gets an uplink.

| GUI | Runs on | Port | URL | Status |
|---|---|---|---|---|
| Camera dashboard | `tile-pi` | 5000 | `http://192.168.1.11:5000/` | exists (`camera_node/.../config.yaml`) |
| Acoustic dashboard | `tile-pi` | 5001 | `http://192.168.1.11:5001/` | **reserved** — acoustic has no web GUI yet (matplotlib only) |
| Pick-and-place twin | `tile-gantry` | 5050 | `http://192.168.1.15:5050/` | exists (`pick_place/config.yaml`) |
| Pick-and-place I/O bench test | `tile-gantry` | 5060 | `http://192.168.1.15:5060/` | exists (`pick_place_node/bench_tests/io_smoke_test/`); bench only, not run alongside the twin |
| Master dashboard | `tile-master` | 8000 | `http://192.168.1.10:8000/` | **reserved** — not built |
| MQTT broker | `tile-master` | 1883 | `mqtt://192.168.1.10:1883` | per §12; plain, unauthenticated |

Port conflicts to watch: on `tile-pi` the camera and acoustic services must not share a
port; 5000 is also the default for other Flask apps.

---

## 4. Message layer

**Decided earlier (not by this plan):** MQTT over the wired LAN, broker on the master PC,
topic structure in `Automation_Architecture.md` §12 (`tile/<id>/camera/result`,
`machine/robot/command`, …). This network plan changes none of that.

**Consequence of the shared Pi:** `tile-pi` publishes *both* `tile/<id>/camera/result`
and `tile/<id>/acoustic/result`, from two separate processes, under two MQTT client IDs.
The master must not assume one publisher per station.

**The Nano is not an MQTT client.** The Pi's acoustic service reads the Nano's tap events
over USB serial and publishes the acoustic result on its behalf. Belt-speed commands from
the master reach the Nano the same way: master → MQTT → `tile-pi` → serial → Nano.
This puts the Pi in the control path of the belt — see §7.

---

## 5. The Pi's two networks

The Pi joins the **external Wi-Fi** (internet, for installs) and the **control LAN**
(router) at once. The router's DHCP will advertise itself as the default gateway, but it
has no uplink, so a Pi that believed it would lose internet or have traffic black-holed.

- Mark the router connection **"never use this network for default route"** (NetworkManager:
  `ipv4.never-default yes` on that connection) so the external Wi-Fi keeps the default route.
- Control traffic to `192.168.1.0/24` still goes out the router interface, because it is a
  directly-connected subnet.
- Prefer the router on **wired Ethernet** and the external link on Wi-Fi, so the two never
  contend for the one radio.
- None of this has been tried. Verify with `ip route` on the Pi once both are up.

---

## 6. Time without internet

No NTP means clocks drift and tile records from different nodes won't line up.
**Open — proposal:** the master PC (or the Pi) serves time (chrony/ntpd) to the LAN, and
nodes sync to it. The Pi has **no battery-backed RTC by default**, so after a power cut it
boots with a stale clock until it syncs. Until this is settled, treat cross-node
timestamps as approximate and correlate on `tile_id`, not on time.

---

## 7. Security and failure notes

- Change the router admin password (§2.1); factory `admin`/`admin` is unacceptable even
  on an isolated LAN because the Wi-Fi is reachable from outside the room.
- No auth on MQTT or the Flask pages. Acceptable only while there is no uplink.
- Do not plug the router's WAN port into any other network without revisiting this file.
- **Single point of failure:** the router (everything), and `tile-pi` (vision + audio +
  the Nano's only path to the master). Acceptable for the Phase 1 lab prototype.
- **E-stops are separate (owner, 2026-10-10):** the Nano's e-stop cuts power upstream of
  it; the UNO Q uses the CNC-shield e-stop. Neither depends on the network. Pressing one
  does not stop the other half — see `Automation_Architecture.md` §5.5.
- **Belt safety must not depend on the network.** The Nano is reached through the Pi,
  which is reached through the router. If any link drops, the Nano should stop the belt on
  a serial/heartbeat timeout by itself. (Firmware requirement — not yet written.)

---

## 8. Open items

- [ ] Read router H/W revision + firmware version; record below.
- [ ] Configure router per §2.1; record real menu names.
- [ ] Collect MACs for all nodes; fill §3; enter reservations.
- [ ] Test the Ethernet hub on the UNO Q (`lsusb`, `ip link`, DHCP lease, power passthrough); record hub model and MAC.
- [ ] Update `tools/uno_q/config.bat` (`UNO_Q_HOST`, currently a hotspot lease `172.20.10.x`)
      once the UNO Q is on the router; the pinned SSH host key should be unchanged.
- [ ] Decide time source (§6).
- [ ] Re-confirm node list with teammates; update §3 and `Automation_Architecture.md`.
- [ ] Verify the Pi's dual-network routing (§5).

## 9. Router record (fill in when known)

| | |
|---|---|
| Model | D-Link DIR-825 AC1200 |
| H/W revision | _not read yet_ |
| Firmware version | _not read yet_ |
| Reset to factory | yes (owner, 2026-10-10) |
| Configured | **no** |
