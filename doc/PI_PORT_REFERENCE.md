# PL Connect — Raspberry Pi Port: Maintainer Reference

_Companion to `README.md` / `SETUP.md` (user-facing install docs) and to the
firmware-side `project_analysis.md` / `PROTOCOLS.md` / `RS485_MODBUS_RTU_CONTEX.md`
/ `rpi4_esp32p4_gpio_reference.md` in this same `doc/` folder (those describe the
**ESP32-P4 firmware**, not this codebase — kept here for cross-reference while
porting). This file documents **this repo** — the Python/Flask port — for anyone
(human or AI) maintaining it or porting a future firmware change across._

**Keep this file up to date.** Whenever `app/*.py` or `web/*` changes in a way
that affects an API shape, a config field, a pin mapping, or a known
deviation from the firmware, update the matching section below in the same
change. Treat it like `project_analysis.md` on the firmware side — a living
doc, not a one-time snapshot.

---

## 1. What this is

A Python/Flask port of the ESP32-P4 "PL Connect" firmware for the Raspberry
Pi variant of the same carrier board — **runs on Pi 4 and Pi 5 both**. Same
REST/WebSocket/MQTT/Modbus API, same dashboard (HTML/CSS/JS extracted
near-verbatim from the firmware's embedded C string assets), same register
maps. The porting goal was byte-for-byte API compatibility so the extracted
`web/` frontend runs unmodified against this backend.

The 40-pin header's BCM GPIO numbering (§6) is identical across Pi 4 and
Pi 5, so the pin map and every module's logic is shared unmodified between
the two boards. The one place a Pi generation actually mattered: real
`RPi.GPIO` talks to BCM2711 registers directly and cannot address the Pi 5's
RP1 I/O chip at all — fixed by switching to `rpi-lgpio` (§12), a drop-in
replacement under the same `import RPi.GPIO` that works on both.

Current version string (`app/state.py::FIRMWARE_VERSION`): **1.1.4**. This
field is manually maintained — bump it in the same change whenever
`app/state.py`'s `build_status_json()` shape, a REST endpoint, or a config
schema changes, so the dashboard's footer and `/api/status` stay a useful
signal of what's actually running on a given device.

---

## 2. Repo layout

```
iot-webserver-pi/
├── app/                     — Python backend, one module per firmware C module
│   ├── config.py            — JSON persistence (was NVS namespaces)
│   ├── gpio_driver.py       — relay/DI driver, scan task, counters (port of gpio.c)
│   ├── http_server.py       — Flask routes: REST + /ws + static/page routes
│   ├── main.py              — entrypoint, boot sequence (port of main.c app_main())
│   ├── mqtt_manager.py      — publish-only MQTT client (port of mqtt_manager.c)
│   ├── modbus_tcp_slave.py  — Modbus TCP slave, read-only (port of modbus_manager.c)
│   ├── modbus_rtu_master.py — RS485 Modbus RTU master (port of modbus_rtu_manager.c)
│   ├── network_manager.py   — LAN/Wi-Fi via nmcli (port of network.c + wifi_manager.c)
│   ├── oled_display.py      — SSD1306 IP display (port of oled_display.c)
│   ├── sd_notify.py         — systemd readiness/watchdog ping (no firmware equivalent)
│   ├── state.py             — shared status JSON builder + WS broadcast hub
│   └── updater.py           — git-based /update mechanism (no firmware equivalent)
├── web/
│   ├── pages/                — index.html + lazy-loaded page fragments + update.html
│   └── static/{app.js,style.css}
├── data/                    — runtime JSON config, created on first run (gitignored)
│   ├── system.json          — network + MQTT + Modbus TCP config
│   ├── channels.json        — DI/DO labels/modes/invert + DI counters
│   ├── rs485.json           — RS485 bus + slave/register config
│   ├── update.json          — git remote/branch/auth for /update (secrets included)
│   └── deploy_key           — SSH private key for git auth, 0600, if configured
├── doc/                     — this file + firmware-side reference docs
├── pl-connect.service       — systemd unit
├── requirements.txt
├── README.md / SETUP.md     — user-facing quick reference / full setup walkthrough
└── venv/                    — local virtualenv (gitignored)
```

---

## 3. Boot sequence (`app/main.py`)

Mirrors `main.c`'s `app_main()` order:

```
main()
  ├─ config.load_config()
  ├─ gpio_driver.gpio_init()
  ├─ gpio_driver.start_input_scan()      # scan thread (50ms) + counter-save thread (10s)
  ├─ network_manager.init(sys_cfg)       # LAN always applies; Wi-Fi only if enabled
  ├─ mqtt_manager.init(sys_cfg)          # no-op if disabled
  ├─ modbus_tcp_slave.init(sys_cfg)      # no-op if disabled
  ├─ modbus_rtu_master.init(rs485_cfg)   # no-op if disabled
  ├─ oled_display.init()                 # logs + skips on failure, never aborts
  ├─ apply saved DI modes + invert flags
  └─ http_server.start()                 # blocking — Flask app.run()
```

Every optional module (`mqtt_manager`, `modbus_tcp_slave`, `modbus_rtu_master`,
`oled_display`) follows the same failure-tolerance pattern as the firmware:
missing dependency or disabled config → log and return, never raise/abort boot.

---

## 4. Config persistence (`app/config.py`, `data/*.json`)

Three JSON files replace the firmware's three NVS namespaces. Each `save_*`
function does a **partial update**: a field missing or wrong-typed in the
incoming dict leaves the stored value untouched (mirrors the C source's
partial-update semantics exactly — do not change a `save_*` function to
require full payloads without checking every caller in `app.js`).

### `system.json` (was NVS `system`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `wifi_ssid` / `wifi_pass` | str | `"Your_SSID"` / `""` | |
| `wifi_ip/subnet/gateway/dns` | str | `192.168.1.101` / `255.255.255.0` / `192.168.1.1` / `""` | |
| `wifi_static` / `wifi_enabled` | bool | `true` / `false` | |
| `lan_ip/subnet/gateway/dns` | str | same defaults as Wi-Fi | |
| `lan_static` | bool | `true` | |
| `mqtt_enabled` | bool | `false` | |
| `mqtt_broker/port/topic/interval` | str/int | `broker.hivemq.com` / `1883` / `esp32/status` / `1000` | interval min-clamped to 250ms at publish time |
| `mb_enabled/port/unit_id` | bool/int/int | `false` / `502` / `1` | Modbus TCP slave |

### `channels.json` (was NVS `channels`)

`{"di": [{label, mode, invert} × 10], "do": [{label, mode, invert} × 2], "counts": [int × 10]}`.
Label max 24 chars (`CH_LABEL_LEN`). `mode`: DI = 0 Normal/1 Counter/2 Frequency
(Frequency unimplemented, same as firmware); DO `mode`/`invert` are
**informational only** — no setter exists, relay output is always direct
on/off (`gpio_driver.set_relay`), matching the firmware's DO behavior exactly.

### `rs485.json` (was NVS `rs485`)

```
{
  enabled, baud, parity (0/1/2 = None/Odd/Even),
  data_bits (5-8), stop_bits (0/1/2 = 1/1.5/2),
  slaves: [
    { enabled, slave_id (1-247), poll_interval_ms, label,
      registers: [
        { reg_type (0=Holding/1=Input/2=Coil/3=Discrete),
          start_addr, data_type (0=U16/1=S16/2=U32/3=S32/4=F32/5=BIT),
          label, scale, unit }
        × up to RS485_MAX_REGS_PER_SLAVE=16
      ] }
    × up to RS485_MAX_SLAVES=20
  ]
}
```

`save_rs485_config` does a **wholesale replace** of the `slaves` array
(capped at 20), unlike the firmware's fixed-size NVS slot array which needed
an explicit "clear stale slots" step to avoid ghost slaves — a plain JSON
list has no leftover slots by construction, so that firmware bug class does
not exist here. `data_type=BIT` is forced server-side whenever
`reg_type` is Coil/Discrete (`_validate_register`).

### `update.json` (no firmware equivalent — new for git-pull updates)

`{update_password, repo_url, branch, auth_method (none/token/ssh_key), token}`.
Never returned in full via the API — `config.redact_update_config()` replaces
`token`/`update_password` with `token_set`/`password_set` booleans. Default
password is **`123456`**, same "casual deterrent, not real auth" spirit as
the firmware's hardcoded OTA password — change it before exposing off-LAN.

---

## 5. REST API (`app/http_server.py`)

All paths/methods/JSON field names mirror the firmware exactly so `web/`
runs unmodified. If you add or change an endpoint, update `app.js` in the
same change — there is no API versioning layer.

| Endpoint | Method | Purpose | Side effect |
|---|---|---|---|
| `/`, `/style.css`, `/app.js`, `/page/<io\|config\|network\|system\|rs485>` | GET | dashboard shell + fragments | — |
| `/api/status` | GET | full status JSON (see §6) | — |
| `/api/relay?id=&state=` | GET | set relay | live |
| `/api/channel-config` | GET | DI/DO config | — |
| `/api/save-channel-config` | POST | save DI/DO config | live (no restart) |
| `/api/reset-counter?ch=` | GET | reset DI counter (`-1`=all) | live |
| `/api/network-config` | GET | Wi-Fi + LAN config | — |
| `/api/save-wifi-config` / `/api/save-lan-config` | POST | save + apply via nmcli | **service restart** |
| `/api/mqtt-config` / `/api/save-mqtt-config` | GET/POST | MQTT config + live status | restart on save |
| `/api/modbus-config` / `/api/save-modbus-config` | GET/POST | Modbus TCP config + status | restart on save |
| `/api/rs485-config` / `/api/save-rs485-config` | GET/POST | RS485 config + status | restart on save |
| `/api/reboot` | POST | restart service | restart |
| `/update` | GET | update page (no password required to view) | — |
| `/update` | POST | git fetch+checkout, password via `X-OTA-Password` header | restart on success |
| `/api/update-config` | GET | redacted update config | — |
| `/api/save-update-config` | POST | save update config, **requires** `X-OTA-Password` | — |
| `/ws` | WebSocket | full status snapshot on connect, then push on any change | — |

**"Restart on save"** = `http_server.schedule_restart()`: sleeps 0.5s, then
`os._exit(0)`. This relies on the systemd unit's `Restart=always` to relaunch
the process — it is **not** a graceful reload. Running via `python -m
app.main` directly (dev/test) means a config save that triggers a restart
just kills the process with nothing to relaunch it; that's expected for
local testing (see README's "Testing locally" section).

### `/api/status` and WebSocket push shape

Identical to `GET /api/status` for the WS push; also the payload published
to MQTT. Built once in `state.build_status_json()` — do not create a
separate serializer for a new channel; extend this function and it flows
into REST/WS/MQTT automatically, same rule the firmware follows.

```json
{
  "ip": "192.168.1.101", "wifi_ip": "", "wifi_connected": false,
  "version": "1.1.4", "copyright": "...",
  "board_model": "Raspberry Pi 5 Model B Rev 1.0", "board_cpu": "Broadcom BCM2712",
  "relays": [false, false],
  "inputs": [false, ...×10],
  "counts": [0, ...×10],
  "modbus_rtu": [
    { "id": 1, "label": "Slave 1",
      "registers": [{"label": "Reg 1", "value": 23.5, "unit": "V"}],
      "online": true, "last_update_ms": 918234.0 }
  ]
}
```

Note: the Pi port's `modbus_rtu` shape nests values under `registers[]`
(label+value+unit per register) rather than the firmware's flatter
`values[]`/`reg_labels`/`reg_units` parallel arrays — `app.js`'s
`renderModbusRtu()` was written against this nested shape. If reconciling
with a firmware change to this block, check both shapes match what `app.js`
expects before assuming a straight copy-over is safe.

---

## 6. GPIO pin map (Pi BCM numbers — re-derived from the board schematic,
**not** the ESP32 firmware's GPIO numbers, which are meaningless here)

| Signal | Pi BCM GPIO | Header pin |
|---|---|---|
| DO0 (relay 0) | 22 | 15 |
| DO1 (relay 1) | 27 | 13 |
| DI0..DI9 | 16,19,20,26,21,25,5,12,6,13 | 36,35,38,37,40,22,29,32,31,33 |
| RS485 TX/RX | 14/15 (hw UART0, `/dev/serial0`) | 8/10 |
| RS485 DE/nRE | 23/24 | 16/18 |
| OLED SDA/SCL | 2/3 (hw I2C1) | 3/5 |

Source of truth for these constants in code: `app/gpio_driver.py`
(`DO_PINS`, `DI_PINS`), `app/modbus_rtu_master.py` (`RTU_DE_GPIO`,
`RTU_NRE_GPIO`, `RTU_SERIAL_PORT`), `app/oled_display.py` (`i2c(port=1,
address=0x3C)`). `web/static/app.js` also hardcodes `DO_PINS`/`DI_PINS` for
display labels only — keep in sync if the physical mapping ever changes.

Requires `/boot/firmware/config.txt`: `dtparam=i2c_arm=on`,
`enable_uart=1`, `dtoverlay=disable-bt` (frees the PL011 UART from
Bluetooth onto GPIO14/15). See `SETUP.md` §2 for the full one-time OS setup.

---

## 7. RS485 Modbus RTU master (`app/modbus_rtu_master.py`)

Biggest structural difference from the firmware. The ESP32's UART peripheral
can hardware-auto-drive any GPIO as DE via `UART_MODE_RS485_HALF_DUPLEX`; the
Pi's UART has no such per-pin remap, so DE (GPIO23) is a **plain GPIO
toggled manually** around each transaction:

```
DE high → sleep RTU_DE_SETTLE_S → write → flush → sleep (computed, see below)
  → DE low → discard self-echo → read response
```

Framing is **hand-rolled** (`_crc16`, `_build_request`, `_transact`) rather
than a library — chosen so the decode logic can mirror the firmware's traced
esp-modbus behavior exactly: a single coil/discrete read's value is always
bit 0 of the response data byte, and multi-word (U32/S32/F32) values assume
big-endian **word** order (first register = high word) on top of each
register's own big-endian **byte** order.

Function code selection (`_FUNC_BY_REG_TYPE`): Holding→0x03, Input→0x04,
Coil→0x01, Discrete→0x02. Timeout `RTU_RESPONSE_TIMEOUT_S = 0.3` — same
300ms floor as the firmware's `CONFIG_FMB_MASTER_TIMEOUT_MS_RESPOND`, so one
dead slave/register can't stall a multi-slave poll round. Poll loop is
sequential across all slaves/registers (`RTU_POLL_TICK_S = 0.02` between
ticks) — RS485 is half-duplex, only one outstanding request at a time,
matching the firmware's design constraint.

A slave's round is aggregate pass/fail: all its registers must succeed to
mark `online=true` and update `values`; a slave goes `online=false` once
stale beyond 3× its poll interval (same rule as firmware).

### Real-hardware bring-up findings (this port, not the firmware)

Bench-tested against a pymodbus-based RTU slave simulator over a real
USB-RS485 link. Three genuine bugs found and fixed, in the order they
surfaced — worth reading in order if debugging a similar symptom again:

1. **Self-echo, not wiring.** The original code's assumption — "nRE fixed
   low, receiver always enabled; half-duplex framing alone prevents
   self-echo confusion since reads only start after the write is confirmed
   flushed" — was simply wrong. That reasoning describes the ESP32's
   `UART_MODE_RS485_HALF_DUPLEX`, which suppresses self-reception **in
   silicon**. Plain bit-banged GPIO DE has no such feature: with the
   receiver always on, our own transmitted bytes land in the OS receive
   buffer during TX, and since nothing discarded them, the *first* thing
   `_transact()` read back after transmitting was its own request,
   misparsed as a response. Symptom: every failure showed a "response"
   whose address/function bytes exactly matched whatever was just
   transmitted, and CRC always failed (the tail bytes aren't a real CRC for
   that reinterpreted framing) — externally this looked like "polling
   random slave IDs" and "100% CRC errors," which is what it took to
   diagnose. **Fix:** `ser.reset_input_buffer()` a second time, right after
   DE goes low, before listening for the real reply.
2. **DE dropped mid-frame.** After the self-echo fix, real data got through
   — but the simulator kept seeing frames truncated to a fraction of a full
   8-byte request, with the captured "address" bytes always some power of
   two (`0x21, 0x20, 0x10, 0x08, 0x01, 0x00` etc. — a live-corruption
   signature, not noise). Root cause: `ser.flush()` (`tcdrain()`) is
   supposed to block until the UART has *physically* finished clocking
   every bit onto the wire, but this isn't reliable on every Linux
   UART-driver combination — some report "drained" once the last byte
   reaches the hardware FIFO, not once the FIFO has actually emptied. The
   old fixed 2ms post-write settle before dropping DE was too close to an
   8-byte frame's own ~4.2ms transmit time at 19200 baud to reliably cover
   FIFO drain slop. **Fix:** compute the frame's actual nominal transmit
   time from the real `ser.baudrate`/`bytesize`/`parity`/`stopbits`, and
   wait at least that long (×1.25 margin) before dropping DE, instead of
   trusting a flat guess.
3. **No recovery from a genuinely broken port.** Under sustained polling,
   the port eventually started raising `OSError` (`errno 5`, "Input/output
   error") on every read/write, forever — a real UART/kernel-level fault
   that doesn't self-clear. The poll loop had no way to tell this apart
   from a normal Modbus-level failure (CRC mismatch, timeout), so it just
   kept hammering a dead file descriptor. **Fix:** introduced
   `RtuProtocolError` for expected Modbus-level failures (deliberately
   *not* an `OSError` subclass, unlike Python's built-in `TimeoutError`,
   which is one) so the poll loop can specifically catch `OSError`/
   `serial.SerialException` and close+reopen the port, giving a transient
   hardware fault a chance to actually clear.

All three fixes are code-verified (compiles, unit-testable in isolation) but
the *combination* was not re-confirmed end-to-end on real hardware after fix
#3 landed — if picking this up again, that's the next thing to check.

---

## 8. Modbus TCP slave (`app/modbus_tcp_slave.py`)

Read-only via pymodbus, register map **unchanged** from firmware:

| Data | Table | FC | Offset | Count |
|---|---|---|---|---|
| DI0..DI9 | Discrete inputs | FC02 | 0-9 | 10 |
| Relay DO0/DO1 | Discrete inputs | FC02 | 10-11 | 2 |
| DI counters (32-bit, low word first) | Input registers | FC04 | 0-19 | 20 |

Coils/holding registers are deliberately made un-addressable
(`_NoAccessBlock`, always `validate()→False`) so FC01/03/05/06/15/16 all
return a Modbus exception — matches the firmware's "no writable areas"
design and `PROTOCOLS.md`'s documented behavior regardless of pymodbus's own
defaults for an unconfigured store. Sync loop copies GPIO state into the
Modbus context every 50ms (`_SYNC_PERIOD_S`), same cadence as the firmware's
`mb_sync` task.

Written and tested against **pymodbus 3.x**'s
`ModbusSlaveContext.setValues(fx, address, values)` signature — check this
first if upgrading pymodbus to a different major version.

---

## 9. Update mechanism (`app/updater.py`) — Pi-only, no firmware equivalent

Replaces OTA binary flashing (meaningless for a git-checkout deployment).
`POST /update` does `git fetch origin <branch>` + `git checkout -B <branch>
origin/<branch>` in the app directory, then restarts the service. This is a
**hard reset to match origin**, not a merge — any local edits made directly
on the Pi are expected to be disposable on that branch (git still refuses,
reported as a failure, if uncommitted changes would be clobbered).

Two auth methods, both applied **only to the single git subprocess call**,
never persisted to `.git/config` (so `git remote -v` on the Pi never leaks a
credential):

- **token**: HTTPS PAT sent as a Basic-auth header via `-c
  http.extraheader=...` (ephemeral, not embedded in the remote URL).
- **ssh_key**: private key content written once to `data/deploy_key` (0600),
  pointed at via `GIT_SSH_COMMAND` for that call only.

Gated by `X-OTA-Password` header checked against `update.json`'s
`update_password` (default `123456`) — same casual-deterrent, not-real-auth
spirit as the firmware's hardcoded OTA password. `/api/save-update-config`
is the **only** config-save endpoint in the whole app that requires this
password, since it's the one endpoint that stores credentials used to
pull-and-run arbitrary code as whatever user the service runs as (root, by
the default systemd unit).

Requires the app directory (or an ancestor) to be a real git checkout with a
working `origin` remote — a `cp -r` deploy instead of `git clone` silently
breaks this feature. See `SETUP.md` §4 and §8.

---

## 10. Deliberate deviations from the ESP32 firmware (checklist)

- [x] Network config apply gated behind `PL_CONNECT_APPLY_NETWORK=1` env var
      — firmware always applies LAN config at boot; doing that unconditionally
      on arbitrary dev hardware would repoint a real NIC (happened once
      during this port's own first test run — see `network_manager.py`'s
      module docstring). **Do not remove this gate** without a better
      safeguard in its place.
- [x] "Reboot to apply" → service restart (`os._exit(0)` + systemd
      `Restart=always`), not a full Pi OS reboot.
- [x] OTA firmware upload → git-based `/update` (fetch + hard checkout),
      entirely new subsystem with its own password gate and credential
      storage (`update.json`, `deploy_key`).
- [x] RS485 DE line: hardware-auto-toggled UART RTS on the ESP32 vs.
      manually-toggled plain GPIO on the Pi (§7) — a real functional
      difference in timing precision, not just an implementation detail.
- [x] Modbus RTU status JSON nests registers under `registers[]` per slave
      instead of the firmware's parallel `values[]`/`reg_labels[]`/
      `reg_units[]` arrays (§5) — `app.js` was written against this shape;
      don't assume firmware JSON-building code ports over verbatim.
- [x] Config storage: JSON files under `data/` instead of NVS namespaces —
      no fixed-size-array "ghost slot" bug class for RS485 slaves, since a
      JSON list replace has no leftover slots by construction (§4).

## 11. Porting checklist — when the firmware side changes

If a change lands in the ESP32 firmware's `main/modules/*.c` and needs to
reach this Pi port, work through in this order:

1. **Read the firmware's own doc trail first** — `project_analysis.md` /
   `RS485_MODBUS_RTU_CONTEX.md` in this `doc/` folder should already
   describe the change and its rationale (version-bump entries at the top
   of `project_analysis.md`).
2. **Identify the matching `app/*.py` module** using the map in §2.
3. **Config schema** — does the firmware change add/rename/resize a field in
   `Config`/`ChannelConfig`/`Rs485Config`? Update the matching
   `DEFAULT_*`/`_validate_*`/`save_*` in `app/config.py` (§4). Note: unlike
   the firmware, a schema change here does **not** need a "breaking NVS
   change, resets on next boot" warning — `_load_raw()`'s `dict.update()`
   over defaults tolerates missing/extra keys gracefully. Still bump the
   in-repo default if the field's meaning changed.
4. **Status JSON** — does it touch `http_server_build_status_json()` on the
   firmware side? Update `state.build_status_json()` the same way, and
   check `app.js`'s corresponding `render*()`/`applyState()` function
   against the new/changed shape (§5's note on the `modbus_rtu` shape
   mismatch is exactly this kind of gap to watch for).
5. **REST endpoints** — new endpoint on the firmware? Add the matching
   Flask route in `http_server.py` with the *same* path/method/JSON field
   names — the whole point of this port is that `web/` doesn't need to
   change. If it must change, update `app.js` in the same commit.
6. **Hardware-specific logic** (GPIO/UART/I2C timing) — do **not** port
   register-level or pin-number specifics from the firmware; only port
   *behavior* (timeouts, retry counts, aggregate-online rules, batching
   cadence). Pin numbers are already re-derived per §6 and must stay Pi BCM
   numbers, never ESP32 GPIO numbers.
7. **Update this file** (§10's deviation checklist, §4/§5/§6/§7/§8 as
   relevant) in the same change.

---

## 12. Pi 4 / Pi 5 dual support

Added after the initial port shipped Pi-4-only. What changed and why:

- **`requirements.txt`**: `RPi.GPIO` → `rpi-lgpio`. Real RPi.GPIO does
  direct register access tied to BCM2711 and raises at `GPIO.setmode()` on a
  Pi 5 (RP1 I/O chip, different register layout entirely). `rpi-lgpio`
  exposes the identical `RPi.GPIO` import/API surface but is backed by
  `lgpio`/`/dev/gpiochip*`, which both SoCs support — **zero code changes**
  needed in `gpio_driver.py` or `modbus_rtu_master.py`, both keep
  `import RPi.GPIO as GPIO` unchanged. Do not have real RPi.GPIO installed
  in the same environment (same import namespace, will conflict) — see
  SETUP.md's troubleshooting note if a system-wide `python3-rpi.gpio` shadows
  the venv's `rpi-lgpio`.
- **`app/state.py`**: added `_detect_board()` (reads
  `/proc/device-tree/model` once at import), exposing `board_model`/
  `board_cpu` in `build_status_json()`. Replaces a hardcoded "Raspberry Pi
  4" / "Broadcom BCM2711" in `page_system.html` that was simply wrong on a
  Pi 5. `app.js`'s `updateNetworkInfo()` sets `#board-model`/`#board-cpu`
  once, same one-shot pattern as the footer version/copyright fields.
- **GPIO pin numbers, UART/I2C device-tree overlays, `nmcli`-based network
  config**: no change needed. Pin numbering is header-standard across Pi
  models; the `config.txt` overlay lines (`dtparam=i2c_arm=on`,
  `enable_uart=1`, `dtoverlay=disable-bt`) are the same names on Pi 5's RP1
  (not independently re-verified on physical Pi 5 hardware during this
  pass — see SETUP.md §2's added caveat); `network_manager.py` already
  detects the Ethernet interface by NetworkManager device *type*, not by a
  hardcoded name, so it doesn't care whether the interface is `eth0` (older
  Pi 4 images) or `end0` (common on newer Bookworm images, more common on
  Pi 5).
- **Not touched**: `luma.oled`/I2C (pure bus I/O via `/dev/i2c-1`, no
  RPi.GPIO dependency), `pyserial`/RS485 (pure `termios`-level serial port
  access), `pymodbus` TCP slave — none of these go through the
  GPIO-register layer that differs between the two SoCs.

## 13. Reliability & factory-deployment hardening

Added in response to real operational issues surfaced while bench-testing
RS485 (§7) — chasing intermittent hardware symptoms is much harder when the
software itself has silent failure modes. All three items below are
general-purpose hardening, not RS485-specific:

- **`network_manager.py`: cached network status.** `get_lan_ip()`/
  `get_wifi_ip()` used to shell out to `nmcli` synchronously on every call —
  and they're called from `state.build_status_json()`, which runs on every
  single `/api/status` request **and** every WebSocket broadcast (i.e. every
  DI/relay change). Under active I/O this meant spawning `nmcli` subprocesses
  many times a second, which both wastes CPU on a Pi and adds scheduling
  jitter that competes with anything timing-sensitive elsewhere (the RS485
  poll thread's millisecond-level DE settle delays, for one — this was
  actually flooding the log during the §7 bring-up work). Now a background
  thread (`_net_poll_loop`, 2s period) refreshes a cache; the getters just
  read it. Side effect: also fixed a pre-existing race on `_iface_cache`
  (only the single poll thread touches `nmcli` now, instead of every
  concurrent Flask request thread).
- **Crash-isolated background loops.** `gpio_driver._scan_loop`/
  `_count_save_loop`, `oled_display._display_loop`,
  `modbus_tcp_slave._sync_loop`, and `modbus_rtu_master`'s poll loop are all
  long-lived `while True` threads. Before this pass, several of them had no
  outer exception handling — one unhandled exception would silently kill
  that subsystem's thread **permanently**, for the rest of the process's
  life, with nothing to notice or restart it (the thread just stops; the
  process itself keeps running fine, which makes this particularly easy to
  miss in the field). All of them now catch, log, and continue on the next
  tick, extending the "never abort, always keep going" philosophy the
  codebase already applies to every peripheral's `init()` into their
  long-running loops too.
- **systemd watchdog (`app/sd_notify.py`, new module).** Pure-socket
  `sd_notify` protocol implementation (no new pip dependency). `main.py`
  calls `sd_notify.ready()` only after every peripheral init has returned
  (they're all individually failure-tolerant, so reaching that call is
  itself a meaningful "actually booted cleanly" signal) and
  `sd_notify.start_watchdog()` at boot, which pings `WATCHDOG=1` at half of
  `WatchdogSec`. `pl-connect.service` changed `Type=simple` → `Type=notify`
  + `WatchdogSec=30`. This catches a genuine **hang** (process alive but
  stuck — e.g. a future blocking call that never returns), which
  `Restart=always` alone cannot detect since it only reacts to the process
  actually exiting. Both `sd_notify` calls are no-ops when `$NOTIFY_SOCKET`
  isn't set, so running the app outside systemd (`PORT=8080 python -m
  app.main`, per README's "Testing locally" section) is unaffected.

## 14. Known gaps (as of this writing)

- RS485 Modbus RTU: three real bugs found and fixed during actual bench
  testing against a real USB-RS485 link (self-echo, DE dropping mid-frame,
  no recovery from a broken port — full writeup in §7's "Real-hardware
  bring-up findings"). Code-verified but **the combination of all three
  fixes was not re-confirmed end-to-end on real hardware** — if picking
  this up again, re-run the same bench test first before assuming anything
  else is broken.
- `app/state.py`'s `last_update_ms` is `time.monotonic() * 1000` — boot-relative,
  not wall-clock, matching the firmware's documented behavior
  (`PROTOCOLS.md` §5) — don't feed it to a wall-clock date function.
- DI Frequency mode (`mode=2`) is a reserved value with no runtime behavior,
  same as firmware — UI allows selecting it but nothing consumes it.
- DO `mode`/`invert` persisted but not acted on (§4) — matches firmware
  exactly, not a bug.
- Slave register **Type** (data_type) is a dashboard field independent of
  what the actual downstream device's register width really is — the
  dashboard doesn't know or validate this against anything. A mismatch
  (e.g. configuring F32 against a device that actually exposes that address
  as U16) won't cause a communication failure or a CRC error — it'll just
  silently decode nonsense values from real bytes. Worth an explicit sanity
  check against the target device's register map whenever a slave is
  configured, not just trusting whatever was picked in the dropdown.
