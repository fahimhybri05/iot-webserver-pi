# Protocols & Data Map — ESP32-P4 I/O WebServer

How each protocol exposes the device I/O, the exact response format, data types, and the
physical-channel → protocol mapping. The device has **2 relay outputs (DO)** and
**10 digital inputs (DI)**.

## Physical channels

| Channel | Direction | GPIO | Notes |
|---------|-----------|------|-------|
| DO0 | Relay output | 6  | `false`=OFF/open, `true`=ON/energized |
| DO1 | Relay output | 20 | |
| DI0 | Digital input | 46 | Input, pull-up. Physical LOW = logical HIGH (active). Optional per-channel invert. |
| DI1 | Digital input | 53 | |
| DI2 | Digital input | 27 | |
| DI3 | Digital input | 47 | |
| DI4 | Digital input | 45 | |
| DI5 | Digital input | 1  | |
| DI6 | Digital input | 33 | |
| DI7 | Digital input | 54 | |
| DI8 | Digital input | 26 | |
| DI9 | Digital input | 48 | |

**DI modes** (per channel, set in Channel Config): `0` Normal · `1` Counter · `2` Frequency.
Counters increment on the rising edge of the (post-invert) logical state, only in Counter mode.

All protocols read the **same** in-memory state (updated by the 50 ms DI scan task), so
values always agree across HTTP, WebSocket, MQTT, and Modbus TCP. The `modbus_rtu` block
(see §5) is a separate data source — values polled from external RS485 slaves by their
own independent poll task, not derived from local DI/DO state — but it's still folded
into the same shared JSON builder, so it appears consistently across REST/WS/MQTT too.

---

## Access paths at a glance

| Protocol | Transport | Direction | Format | Controls relays? |
|----------|-----------|-----------|--------|------------------|
| HTTP REST | TCP 80 | request/response | JSON (text) | Yes (`/api/relay`) |
| WebSocket | TCP 80 `/ws` | server push | JSON (text) | No (read-only stream) |
| MQTT | TCP to broker | publish-only | JSON (text) | No |
| Modbus TCP | TCP 502 | request/response | binary registers | **No — read-only** (device is the slave) |
| Modbus RTU | RS485 (UART1) | device polls externally | binary registers | **No — read-only** (device is the master, polls external slaves) |

---

## 1. HTTP REST (port 80)

Text JSON over HTTP. Full control + config surface. Every JSON response sends
`Connection: close` — the device closes the underlying TCP connection after each reply
rather than keeping it alive, so clients should expect (and are fine with) one connection
per request rather than reusing a persistent connection for repeated polling.

### `GET /api/status` → status JSON
```json
{
  "ip": "192.168.100.17",
  "wifi_ip": "",
  "wifi_connected": false,
  "version": "1.0.6",
  "copyright": "2025 PL Connect. All rights reserved.",
  "relays": [false, false],
  "inputs": [false, false, false, false, false, false, false, false, false, false],
  "counts": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  "modbus_rtu": [
    { "id": 1, "label": "Slave 1", "values": [235], "online": true, "last_update_ms": 918234 }
  ]
}
```

| Field | Type | Meaning |
|-------|------|---------|
| `ip` | string | Ethernet IPv4 |
| `wifi_ip` | string | Wi-Fi IPv4 (empty if not connected) |
| `wifi_connected` | bool | Wi-Fi STA link state |
| `version` | string | Firmware version |
| `copyright` | string | Copyright line |
| `relays` | bool[2] | DO0, DO1 — `true`=ON |
| `inputs` | bool[10] | DI0..DI9 — `true`=active (post-invert) |
| `counts` | number[10] | Per-DI 32-bit counters (0 unless Counter mode) |
| `modbus_rtu` | array | One object per **configured** RS485 slave (see §5) |

### Other endpoints
| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/relay?id=<0-1>&state=<0-1>` | GET | Set a relay (only write path to outputs) |
| `/api/channel-config` | GET | Per-channel labels/modes/invert (JSON) |
| `/api/save-channel-config` | POST | Save channel config (applied live, no reboot) |
| `/api/reset-counter?ch=<n>` | GET | Reset counter (`-1` = all) |
| `/api/network-config` | GET | Wi-Fi + LAN config incl. DNS |
| `/api/save-wifi-config` / `/api/save-lan-config` | POST | Save → reboot |
| `/api/mqtt-config` / `/api/save-mqtt-config` | GET / POST | MQTT config (+status) / save → reboot |
| `/api/modbus-config` / `/api/save-modbus-config` | GET / POST | Modbus TCP config (+status) / save → reboot |
| `/api/rs485-config` / `/api/save-rs485-config` | GET / POST | Modbus RTU config (+status) / save → reboot |
| `/api/reboot` | POST | Reboot |
| `/`, `/style.css`, `/app.js`, `/page/*` | GET | Web UI assets |
| `/update` | GET/POST | OTA firmware page / upload |

---

## 2. WebSocket (port 80, `/ws`)

Same status JSON as `GET /api/status`, but **pushed** by the device whenever I/O state
changes (relay set, DI edge, counter reset) — no polling. On connect the device sends one
immediate snapshot. Payload schema is identical to the status JSON above. Read-only.

Every push is a **full snapshot** — there is no per-channel or delta message format.
Clients should treat each WS message as a complete replacement of `relays`, `inputs`, and
`counts`, not an incremental patch.

---

## 3. MQTT (publish-only)

When enabled, the device connects to the configured broker and **publishes** the status
JSON (identical schema to `GET /api/status`) to the configured topic every N ms.

| Item | Value |
|------|-------|
| Direction | Publish only (device → broker) |
| Payload | Status JSON (same as REST) |
| Topic | Configurable (default `esp32/status`) |
| Interval | Configurable ms (min 250) |
| QoS / retain | 0 / no |
| Auth / TLS | none (anonymous, plain TCP) |
| Controls relays? | No |

Subscribe to the topic on any MQTT client to receive the JSON. Hostname brokers require
working DNS (set a DNS server on the active interface if using static IP).

---

## 4. Modbus TCP slave (port 502) — binary, read-only

The device is a **Modbus TCP slave/server**. A master (PLC/SCADA/`mbpoll`) reads binary
registers. **No writable areas** — a master can never actuate a relay. Same values as the
JSON, encoded as Modbus registers.

- Default TCP port `502`, unit/slave ID `1` (both configurable).
- `mbpoll -r 1` starts at reference 1 (1-based); reference `N` = protocol offset `N-1`.
- Up to 5 concurrent master connections by default (`CONFIG_FMB_TCP_PORT_MAX_CONN`), each
  idle-timed-out after 20 s (`CONFIG_FMB_TCP_CONNECTION_TOUT_SEC`). Integrators polling
  on a fixed interval should open one TCP connection at startup and reuse it for every
  poll, rather than reconnecting per poll — this uses fewer of the shared connection
  slots and avoids per-poll handshake overhead.

### Register map

| Data | Modbus table | Function | Offset | Count | `mbpoll -t` | 1-based index |
|------|--------------|----------|--------|-------|-------------|---------------|
| DI0..DI9 states | Discrete inputs (1x) | FC2 | 0–9 | 10 | `-t 1` | `[1]`–`[10]` |
| Relay DO0, DO1 states | Discrete inputs (1x) | FC2 | 10–11 | 2 | `-t 1` | `[11]`–`[12]` |
| DI0..DI9 counters | Input registers (3x) | FC4 | 0–19 | 20 | `-t 3` | `[1]`–`[20]` |

> Note on `mbpoll -t`: `0`=coils, `1`=discrete inputs, `3`=input registers, `4`=holding
> registers. This device exposes only discrete inputs (`-t 1`) and input registers (`-t 3`).
> Coils (`-t 0`) and holding (`-t 4`) are not present → a read there returns a Modbus exception.

### Data types & encoding

- **Discrete inputs** — 1 bit each. `0`=false/inactive/OFF, `1`=true/active/ON.
  Bits map: index `[1..10]` = DI0..DI9, index `[11]` = relay DO0, `[12]` = relay DO1.
- **Input registers** — 16-bit unsigned each. Counters are **32-bit**, so each counter uses
  **two consecutive registers, low word first**:
  - counter `i` (i = 0..9) → registers at offset `2*i` (low 16 bits) and `2*i+1` (high 16 bits).
  - Value = `reg[2*i] + (reg[2*i+1] << 16)`.
  - In `mbpoll -t 3 -r 1` output: counter `i` = `[2*i+1] + ([2*i+2] << 16)`.
  - Example: counter 0 = index `[1]` (low) + `[2]` (high); counter 3 = `[7]` + `[8]`.

### Example commands
```bash
# DI states + relay states (12 discrete inputs)
mbpoll -m tcp -a 1 -t 1 -r 1 -c 12 -1 192.168.100.17

# Counters (20 input registers = 10 x 32-bit)
mbpoll -m tcp -a 1 -t 3 -r 1 -c 20 -1 192.168.100.17

# Both, once each, on one line
mbpoll -m tcp -a 1 -t 1 -r 1 -c 12 -1 192.168.100.17 && \
mbpoll -m tcp -a 1 -t 3 -r 1 -c 20 -1 192.168.100.17
```

---

## 5. Modbus RTU master (RS485, UART1) — binary, read-only, device is the **master**

Unlike every other protocol above (where the device is polled or pushes its own I/O
state), here the device is the **initiator** — it polls one or more external Modbus RTU
slave devices (sensors, meters, PLCs) wired to its RS485 bus, and republishes what it
reads through the same `modbus_rtu` JSON block shown in §1. **Read-only** — no write
function codes (06/16) implemented, so this device can never write to a slave it polls.

- UART1, 9600 baud 8N1 by default (configurable), GPIO14/15/23/24 (see README hardware
  section). Sequential polling — RS485 is half-duplex, only one outstanding request
  across all configured slaves at a time. Realistic round time therefore scales with
  slave count (~30-100ms per healthy slave at 9600 baud) — raise baud or space out
  poll intervals for larger slave counts.
- Per slave: slave ID, register area (holding `0x03` / input `0x04` / coil `0x01` /
  discrete `0x02`), start address, register count (≤16), poll interval, label, and a
  scale factor applied as `value = raw_register × scale` (e.g. `scale=0.1` turns a raw
  `235` into `23.5`). Coil/discrete registers are single-bit reads (`data_type=BIT`,
  forced by the dashboard whenever area=Coil/Discrete) — `value` is `0.0` or `1.0 ×
  scale`, not a decoded word.
- CRC mismatch, timeout, or wrong slave-id/function-code in a reply → discarded
  silently (`ESP_LOGW` only), slave stays/goes `online:false` — never crashes or hangs
  later polls on the bus. Per-request response timeout is a **compile-time** constant
  (`CONFIG_FMB_MASTER_TIMEOUT_MS_RESPOND=300`, not runtime/dashboard-configurable) tuned
  low so one dead slave can't stall a full poll round for seconds at higher slave
  counts.
- Config stored in NVS namespace `rs485`; array-backed for up to **20 slaves**
  (`RS485_MAX_SLAVES`) — dashboard has a dynamic add/remove list, not a fixed form.
- WebSocket push is batched to once per completed poll round (not once per slave) —
  matters once multiple slaves are configured and due around the same time.

### `modbus_rtu` JSON block (per configured slave)

| Field | Type | Meaning |
|-------|------|---------|
| `id` | number | Slave ID (1–247) |
| `label` | string | User-assigned label |
| `values` | number[] | Parsed register values, already scaled; empty until first good poll |
| `online` | bool | `true` if a good read landed within 3× the poll interval |
| `last_update_ms` | number | Device's boot-relative monotonic clock (µs/1000) of the last **good** poll — not wall-clock/epoch time, don't feed it to `new Date()` |

An unconfigured slave slot is simply absent from the array (not a null/zeroed entry).

---

## Cross-protocol equivalence

The same DI3 state appears as:
- REST/WS/MQTT JSON: `inputs[3]` (bool)
- Modbus: discrete input offset 3 → `mbpoll -t 1` index `[4]`

Relay DO1:
- JSON: `relays[1]` (bool)
- Modbus: discrete input offset 11 → `mbpoll -t 1` index `[12]`

Counter for DI5:
- JSON: `counts[5]` (number)
- Modbus: input registers offset 10 & 11 → `mbpoll -t 3` index `[11]` (low) + `[12]` (high)

`false` ↔ bit `0`, `true` ↔ bit `1`. All zeros across a poll = idle I/O (no active inputs,
relays off, no counting) — not an error.
