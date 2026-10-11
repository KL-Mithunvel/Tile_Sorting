# Network Plan — D-Link DIR-825 Control LAN

> Companion: `documents/electrical/i2c_bus_plan.md` (the sensor/encoder buses — separate
> from this network).
>
> **Status: PARTLY CONFIGURED** (written 2026-10-10, updated 2026-10-11). The router was
> factory-reset, then set to `192.168.1.1/24` with DHCP `.100`–`.199`; the laptop and the
> UNO Q have both been observed on it, and the UNO Q's DHCP reservation
> (`192.168.1.15`) is verified working (see §3). The node
> list is **provisional** — the team is still revising it (see §3). Decisions that are
> made are marked **Decided**; the rest are **Open**. Observations are dated; anything
> not marked observed is still a plan.

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
- [x] `http://192.168.0.1` (factory) → log in `admin` / `admin` (may be blank on some revisions).
      *(Done 2026-10-11. Choose **Advanced settings**, not the Initial Configuration Wizard —
      the wizard assumes an internet connection. After the IP change the router answers at
      `192.168.1.1`, and logging in there shows "To run the Initial Configuration Wizard the
      device should be reset to factory defaults" — click **Cancel**, never OK.)*
- [ ] **Change the admin password immediately** (Tools / Maintenance → Admin). Keep it
      outside the repo. Factory credentials are known to anyone in Wi-Fi range.
- [x] Setup → Network Settings: Router IP `192.168.1.1`, mask `255.255.255.0`.
      (Chosen to match the existing `192.168.1.x` plan in `Automation_Architecture.md` §23.)
      *(Observed 2026-10-11: LAN page shows `192.168.1.1` / `255.255.255.0`, hostname
      `dlinkrouter.local`.)*
- [x] DHCP server **on**, pool `192.168.1.100` – `192.168.1.199`. Fixed addresses live in
      `.2` – `.99`, outside the pool, so no phone/laptop can take one.
      *(Observed 2026-10-11: mode DHCP, start `.100`, end `.199`, **lease time 1440 min** —
      a client that already holds a lease keeps it for up to 24 h even after a reservation
      is added, see §3.)*
- [ ] Save → router reboots → renew the PC's lease (replug cable or `ipconfig /renew`) →
      log back in at `http://192.168.1.1`.
- [ ] Wi-Fi: SSID `TileLine` (or team's choice), **WPA2-PSK**, strong passphrase kept
      outside the repo. 2.4 GHz is the safe band for the UNO Q; 5 GHz optional.
- [ ] Disable WPS and remote (WAN-side) administration if the UI exposes them.
- [ ] **Turn Telnet off; turn SSH off when setup is done — owner's decision 2026-10-11:
      after the Raspberry Pi is on the LAN and its reservation is in.** *(Observed 2026-10-11: SSH is
      **on**, port 22 open, accepts publickey and password; nothing in this plan needs it.
      Telnet sends the admin password in clear text.)*
- [ ] Add DHCP reservations for every node in §3 (needs each device's MAC). They go under
      the LAN page → **Static IP Addresses → Hosts** (select from connected clients, or
      add: IP address, MAC, hostname).
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
| Master | Owner's PC | `tile-master` | `192.168.1.10` | _TBD_ | Ethernet | PC is master (**Decided**, §5.1). **Observed 2026-10-11:** the laptop's Ethernet is a *manual* static `192.168.1.33/24`, no gateway, no DNS — not `.10` |
| Vision + audio | Raspberry Pi 5 16 GB | `tile-pi` | `192.168.1.11` | **`88:a2:9e:49:e1:ca`** (observed 2026-10-11) | Ethernet | **On the LAN, reservation verified 2026-10-11** (first leased `.150` from the pool, moved to `.11` once the entry existed). OS services, SSH and Wi-Fi not set up |
| Audio (future split) | 2nd Pi / compute unit | `tile-audio` | `192.168.1.12` | _TBD_ | Ethernet | **reserved only** — used if a second unit is bought |
| Measurement | undecided | `tile-measure` | `192.168.1.13` | _TBD_ | Ethernet | node not decided |
| Pick-and-place | Arduino UNO Q | `tile-gantry` | `192.168.1.15` | **`dc:04:5a:9f:0a:1b`** (the hub's adapter, observed 2026-10-11) | **Ethernet via owner's USB-C hub — works** (§3 notes) | lab board named `KLM`. **Reservation works (verified 2026-10-11):** after the entry's IP was corrected from `.142` to `.15` the board's wired link leased `192.168.1.15/24` |
| Striker + belt | Arduino Nano (ATmega328P) | — | **none** | — | USB serial to `tile-pi` | **not on the LAN** |
| Dev laptop | Owner's laptop | — | pool (`.100+`) | — | Wi-Fi/Ethernet | — |

Notes:

- `.14` was the Mega (conveyor controller) in the §23 layout; the Mega is no longer in the
  design (`Automation_Architecture.md` §5.5), so `.14` is left unused rather than reassigned.
- **Decided (2026-10-10):** the UNO Q goes on the router by **Ethernet through the owner's
  USB-C hub**, which satisfies `Automation_Architecture.md` §12's "wired" preference. The
  board's wired MAC is the hub's, so the DHCP reservation follows the hub (swap the hub and
  the address changes).
  **Observed 2026-10-11 (read-only over SSH):** the hub carries a QinHeng/WCH
  `1a86:5394` "USB 10/100 LAN" adapter behind a `1a86:809d` USB2.0 hub; the board's kernel
  binds it with the in-box `cdc_ether` driver as `enxdc045a9f0a1b`, link up, DHCP works.
  Nothing to install. **Still untested:** that the hub powers the board adequately under
  full gantry load (see TODO).
- **Reservation resolved (2026-10-11).** For several bounces the board kept leasing
  `192.168.1.142`. Cause: the Static IP entry had been created by selecting the device
  from the connected-clients list, which pre-fills the device's *current* address, so the
  router reserved `.142`, not `.15` (the row read `192.168.1.142 / DC:04:5A:9F:0A:1B /
  KLM`). Editing the IP to `192.168.1.15` and bouncing the board's wired link
  (`nmcli con down/up 'Wired connection 1'`) gave `192.168.1.15/24` immediately — no
  router reboot or lease release was needed, and **a reservation outside the DHCP pool
  (`.15`) is accepted** by firmware 1.0.5a. **Gotcha for the remaining nodes:** when
  adding a reservation from the clients list, change the IP field before saving.
- **The Pi was misattributed.** `192.168.1.142` / `dc-04-5a-9f-0a-1b`, first seen in the
  laptop's ARP table, is the UNO Q's hub — not the Pi. The Pi's MAC is
  `88:a2:9e:49:e1:ca` (a Raspberry Pi address range), found by ping sweep when it was
  plugged in; it first leased `.150` from the pool, then `.11` after the reservation.
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
- None of this has been tried on the Pi. Verify with `ip route` on the Pi once both are up.

### 5.1 Observed on the UNO Q and the dev laptop (2026-10-11)

**UNO Q — the problem is real, and is fixed.** With the hub on the D-Link, the board had two
default routes: wired via `192.168.1.1` (metric 100) and Wi-Fi via the phone (metric 600).
The wired one won, so its internet traffic went to a router with no uplink. Fixed with
`nmcli con mod 'Wired connection 1' ipv4.never-default yes ipv6.never-default yes` and a
bounce of that connection; afterwards the only default route is Wi-Fi and
`192.168.1.0/24` is still on the wired link. Persistent (stored in the NetworkManager
profile). Undo: the same command with `no`. Note the board is still on the *phone hotspot*
(`172.20.10.3`) for internet and SSH; `tools/uno_q/config.bat` still points there.

**Dev laptop — Ethernet dropped internet (Wi-Fi + Claude + YouTube) when plugged in.**
Observed: Ethernet is manual static `192.168.1.33/24`, no gateway, no DNS; Windows had
auto-metrics Ethernet 5 vs Wi-Fi 45. An IPv4-only metric change did **not** stop the
drop. No Wi-Fi disconnect was logged at the moment of the plug-in, and no `TileLine` Wi-Fi
profile exists on the laptop, so it wasn't Wi-Fi switching. **Working theory, not proven
(the failing route was never captured with the cable in):** the router advertises an IPv6
default route that wins on Ethernet's lower IPv6 metric; apps preferring IPv6 stall.
Applied: Ethernet `InterfaceMetric 500`, `AutomaticMetric Disabled`, for **both** IPv4 and
IPv6 (Wi-Fi stays 45). Stronger fallback if it recurs:
`Disable-NetAdapterBinding -Name "Ethernet" -ComponentID ms_tcpip6`. Undo:
`Set-NetIPInterface -InterfaceAlias "Ethernet" -AddressFamily <IPv4|IPv6> -AutomaticMetric Enabled`.
Applies to the adapter *named* `Ethernet` (Intel I219-LM) only — a USB/dock adapter would
need the same. Keep the laptop's Wi-Fi **on**: it is the only internet path.

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

- [x] Read router H/W revision + firmware version; record below. *(J2, 1.0.5a, 2026-10-11)*
- [ ] Finish configuring the router per §2.1: admin password, Telnet/SSH off, Wi-Fi
      `TileLine` (not seen in a laptop scan on 2026-10-11 — unverified), WPS/remote admin off.
- [ ] Collect MACs for all nodes; fill §3; get **every** reservation honoured. UNO Q done
      (`dc:04:5a:9f:0a:1b` → `.15`, verified); Pi done (`88:a2:9e:49:e1:ca` → `.11`, verified 2026-10-11); laptop still TBD
      (planned `.10`, currently a manual static `.33`).
- [ ] **Pi networking** (next session): enable SSH and record the username; set
      never-default on its wired connection (§5); join the external hotspot / extended
      Wi-Fi — a subnet other than `192.168.1.x` — and verify `ip route`. Then turn the
      router's SSH and Telnet off.
- [x] Test the Ethernet hub on the UNO Q — works (`cdc_ether`, `enxdc045a9f0a1b`, DHCP
      lease). Power passthrough under load still untested.
- [ ] Confirm the laptop no longer drops internet with the cable plugged in (§5.1).
- [x] Update `tools/uno_q/config.bat` (`UNO_Q_HOST`) once the UNO Q is on the router —
      done 2026-10-11, now `192.168.1.15`; SSH over the D-Link LAN verified, pinned host key
      unchanged. Needs the laptop on the D-Link LAN; hotspot `172.20.10.3` is the fallback.
- [ ] Decide time source (§6).
- [ ] Re-confirm node list with teammates; update §3 and `Automation_Architecture.md`.
- [ ] Verify the Pi's dual-network routing (§5).

## 9. Router record (fill in when known)

| | |
|---|---|
| Model | D-Link DIR-825 AC1200 |
| H/W revision | **J2** (read 2026-10-11) |
| Firmware version | **1.0.5a** (read 2026-10-11) |
| LAN MAC | `dc:ea:e7:b0:b4:4f` (laptop ARP table, 2026-10-11) |
| Reset to factory | yes (owner, 2026-10-10, again 2026-10-11) |
| Configured | **partly** — LAN `192.168.1.1/24`, DHCP `.100`–`.199`, lease 1440 min, hostname `dlinkrouter.local`; **SSH on**; admin password, Wi-Fi and reservations not verified |
| UI notes | Advanced settings vs. Initial Configuration Wizard prompt at first login; LAN page has Local IP / Mask / Hostname / DHCP mode + range + lease / DHCP Options / Static IP Addresses → Hosts |
