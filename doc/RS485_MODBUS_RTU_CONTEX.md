# PL Connect — RS485 / Modbus RTU Master: Feature Requirements

_Companion file to `README.md` and `memory/project_analysis.md`. Read both first —
this file only describes the **new** feature being added on top of that existing
architecture._

## Implementation status (as of v1.0.9 firmware, this build)

### Coil (FC01) + Discrete Input (FC02) register support (2026-09-06 pass)

Previously a register was always word-based (holding `0x03` / input `0x04`,
`data_type` U16/S16/U32/S32/F32). Added the two bit-addressed Modbus areas:

- `Rs485RegisterConfig.reg_type` (`config.h`) gains `RS485_REG_COIL=2` /
  `RS485_REG_DISCRETE=3` alongside the existing HOLDING=0/INPUT=1.
- New `Rs485RegisterConfig.data_type` value `RS485_DT_BIT=5` — decoded as a
  single 0.0/1.0 value (× `scale`), never a multi-byte word.
- `modbus_rtu_manager.c` poll loop: `reg_type` (not `data_type`) is the source
  of truth for which Modbus function code to send — `is_bit` is derived from
  `reg_type==COIL||DISCRETE`, mapped to raw command `0x01`/`0x02` (vs.
  `0x03`/`0x04` for holding/input), `reg_size=1`. Decode relies on a traced
  behavior of the vendored `espressif/esp-modbus` serial master (
  `eMBRegCoilsCBSerialMaster`/`eMBRegDiscreteCBSerialMaster` in
  `mbc_serial_master.c`): for a single-bit request the response bit always
  lands at **bit 0** of the response buffer regardless of `start_addr` — so
  decode is just `(((uint8_t*)raw)[0] & 0x01)`, no address-relative bit-shift
  needed. Not yet confirmed against a real coil/discrete slave (same
  real-hardware-verification gap as the rest of this feature — see below).
- Dashboard (`app_js.cpp`): register "Area" dropdown gained Coil/Discrete
  options; picking either now force-sets that row's Type to "Bit" (disabled
  select, single option) via a new `setRtuRegType()` handler — Bit only makes
  sense paired with Coil/Discrete, so the UI doesn't allow the mismatched
  combination. Switching back to Holding/Input resets Type away from Bit to
  U16.
- No NVS schema change — `Rs485RegisterConfig`'s layout is unchanged, only the
  valid range of two existing `uint8_t` fields widened. Existing saved configs
  keep working; validation in `config.c`'s `save_rs485_config` widened to
  accept `reg_type` 0-3 and `data_type` 0-5 (was 0-1 and 0-4).

### Dashboard formatting: 2-decimal values + per-register unit, tab rename, OTA password gate (2026-09-02 pass)

Three small, unrelated changes bundled in one pass:

- **Live values fixed to 2 decimal places.** `renderModbusRtu()` in `app_js.cpp`
  now renders each register value as `Number(r.value).toFixed(2)` instead of the
  raw float (e.g. `120.3` → `120.30`). Display-only — the underlying JSON value
  in `/api/status`/WS/MQTT is still the unrounded scaled float.
- **Per-register display unit.** New `char unit[RS485_UNIT_LEN=12]` field on
  `Rs485RegisterConfig` (`config.h`) — free-text, e.g. `V`/`A`/`kW`, purely
  cosmetic, no effect on decode/scale. Threaded through: NVS blob (raw struct
  copy — same storage pattern as the rest of the register array, no schema
  code changes needed beyond the struct itself, though this **is** a breaking
  NVS schema change like the earlier register-list change — RS485 config
  resets to defaults on next boot); `RtuSlaveStatus.reg_units[][]` in
  `modbus_rtu_manager.h/.c` (copied from config at init, alongside the
  existing `reg_labels[][]`); `unit` key added to both the `/api/rs485-config`
  register JSON and the `modbus_rtu[].registers[]` status JSON in
  `http_server.c`. Dashboard: new "Unit" input column in the register
  sub-table, placed immediately right of "Start Addr" (per explicit request);
  live-values table appends the unit directly after the formatted value, e.g.
  `120.30V`. POST body-size cap for `/api/save-rs485-config` bumped
  49152→57344 bytes to keep margin now that each register's JSON is slightly
  larger.
- **Nav tab reorder + rename.** Tab order changed from `I/O Monitor · Channel
  Config · Network · System · RS485 / Modbus` to `I/O Monitor · Channel Config
  · Network · RS485 · System`; label shortened from "RS485 / Modbus" to
  "RS485" (`index_html.cpp`). Cosmetic only — panel `id`s and `switchTab()`
  wiring unchanged.
- **OTA update page password-gated.** Clicking "Flash Firmware" on `/update`
  now `prompt()`s for a password client-side and sends it as an
  `X-OTA-Password` request header on the upload `POST`. Server-side,
  `ota_update_post_handler` (`http_server.c`) checks the header against a
  hardcoded constant `OTA_UPDATE_PASSWORD = "123456"` before calling
  `esp_ota_begin` — wrong or missing header → `401 Unauthorized`, upload never
  starts (no partial OTA write, no partition touched). Explicitly a
  casual-push deterrent, not real authentication: plain-text password over
  plain HTTP, no TLS anywhere on this device, hardcoded in firmware (visible
  to anyone who dumps/decompiles it). Scripted pushes (`curl`) must add
  `-H "X-OTA-Password: 123456"`.

Version bumped to **1.0.9** (`main/version.h`); copyright year updated to 2026.

### Dynamic per-slave register list (2026-09-01 pass)

Registers are no longer one flat block per slave — each slave now has its own
**dynamic register list** (`Rs485RegisterConfig regs[]`, up to
`RS485_MAX_REGS_PER_SLAVE=16`), each with its own `reg_type` (holding/input),
`start_addr`, `data_type` (U16/S16/U32/S32/F32 — 32-bit types span 2 words,
big-endian word order assumed, unconfirmed against a real target device),
`label`, and `scale`. Dashboard config UI updated to match: each slave renders
as a card with its own nested register sub-table and its own
"+ Add Register"/DEL controls, one level below the existing add/remove-slave
list. Poll task now issues one `mbc_master_send_request()` per register
(was one per slave) — still sequential/half-duplex-safe, just one level
deeper; a slave's online/stale status is now an aggregate over its whole
register round.

Two real bugs caught and fixed during this pass, before flashing:
- **NVS schema**: per-register indexed keys (5 keys x 16 regs x 20 slaves)
  would have been ~1600 NVS entries (~51KB), blowing the 24KB `nvs` partition.
  Switched to one NVS blob per slave (`rtu_rg_%d`, sized by `rtu_rc_%d`)
  instead — worst case ~12KB, no partition resize needed.
- **Stack overflow**: `Rs485Config` grew to ~12KB with the per-slave register
  arrays. It was stack-declared in both `main.c`'s boot sequence (main task
  stack is only 3584 bytes) and `api_rs485_config_get_handler` (httpd worker,
  6144 bytes) — either one would have overflowed on every boot / every config
  page load. Both changed to heap-allocated (`malloc`/`free`), same pattern
  already used for the RtuSlaveStatus snapshot in
  `http_server_build_status_json()`.
- Save endpoint's body-size cap raised from 2048 to 49152 bytes (worst-case
  nested JSON payload is ~40-45KB at 20 slaves x 16 registers), and the
  single `httpd_req_recv()` call changed to a loop — a payload this size is
  very unlikely to land in one TCP read, and the old code didn't check for
  partial reads.

This is a breaking NVS schema change for the `rs485` namespace — RS485 config
resets to defaults on first boot after this update (no migration written,
same precedent as the earlier 4→20 slave-cap change).

### Configurable data bits / stop bits; found + fixed a real OLED bug (same pass)

Added `data_bits` (5-8, default 8) and `stop_bits` (1/1.5/2, default 1) as
bus-level `Rs485Config` fields, alongside the existing baud/parity — goal was
"master fully ready to pull any RS485 device." `mb_communication_info_t` only
exposes mode/port/baudrate/parity, and esp-modbus's own serial init hardcodes
8 data bits / 1 stop bit (traced through `freemodbus/port/portserial_m.c`),
so both are applied as a post-hoc `uart_set_word_length()`/
`uart_set_stop_bits()` override after `mbc_master_start()` — same pattern
already used for the pin/mode remap.

Mark/Space parity and hardware flow control were also asked for, and
explicitly **not implemented** after checking: the ESP32 UART peripheral's
`uart_parity_t` has no Mark/Space mode at all (only None/Even/Odd — checked
`hal/uart_types.h`), and RS485 half-duplex mode already commits RTS to
driving DE, so RTS/CTS flow control can't coexist with it. Neither gap
actually matters for real Modbus RTU traffic — the protocol itself only ever
uses None/Even/Odd parity and has no flow-control concept — which is why
"fully ready to pull any RS485 device" doesn't need either one.

Also found and fixed a real bug in the OLED module (`oled_display.c`) via a
boot log the user shared (`error.md`): `esp_lcd_panel_reset/init/disp_on_off`
were called without checking their return values. When no SSD1306 is
actually wired/responding, these fail with an I2C NACK — but the code
proceeded anyway and created the display task, which then retried
`draw_bitmap()` every 2s forever, spamming "i2c transaction failed" instead
of skipping. Now checked; cleans up (`esp_lcd_panel_del`/`esp_lcd_panel_io_del`/
`i2c_del_master_bus`) and returns early on failure, matching the
never-abort-but-actually-stop-trying pattern used everywhere else.

Also validated (via `PM5330_Modbus_RTU_Register_Guide.pdf`, a Schneider
PowerLogic PM5330 register reference) that `mb_param_request_t.reg_start` is
the raw, zero-based Modbus PDU address — confirmed by the sibling
`mb_reg_start` field's doc comment and by tracing it straight through
`mbc_serial_master.c`/`mbc_tcp_master.c` with no adjustment. So any
documented register number from a device's datasheet needs **-1** before
entering it as `start_addr` in the dashboard (e.g. PM5330's documented 3027
→ enter 3026) — this isn't specific to our firmware, it's the general
Modbus "protocol address vs. documented register number" convention.

### Real-hardware bring-up: a genuine hang found, and a deliberate GPIO remap

First real flash with a physical RS485 device attached surfaced a bug the
static build/review couldn't catch: enabling RTU master hung the boot with
**no error logged and no crash dump** — confirmed via a live serial capture,
boot log stopped dead right after `uart_driver_install`'s "queue free spaces"
line, and the final "RS485 Modbus RTU master up" line never printed.
Root-caused to call order: `uart_set_word_length()`/`uart_set_stop_bits()`
(added in the data-bits/stop-bits pass above) were placed *after*
`uart_set_mode(UART_MODE_RS485_HALF_DUPLEX)` — reconfiguring frame parameters
while that hardware mode is already active (DE tied to TX framing) is the
suspected trigger. Fixed by moving both calls to run *before* the mode
switch. Confirmed fixed on real hardware — full checkpoint sequence now
prints clean through to "RS485 Modbus RTU master up: baud=...".

Also remapped the RS485 GPIOs from the original 14(TX)/15(RX)/23(DE)/24(nRE)
to **37(TX)/38(RX)/5(DE)/4(nRE)**, at the user's request, to free up
14/15/23/24 for another purpose. **Tradeoff, not an oversight:** GPIO37/38 are
also the console UART0 TX/RX pins (confirmed via boot log: "GPIO 38 and 37
are used as console UART I/O pins") — reassigning them to RS485 means no
serial console/logs while this pin assignment is active. Confirmed
acceptable with the user before making the change. GPIO5/4 checked clean
against the existing DO/DI/OLED/Ethernet-RMII map, no conflicts.

**Still open, under active troubleshooting**: with the hang fixed and RTU
master running, every poll of `slave 1 reg 0` times out
(`ESP_ERR_TIMEOUT`) — no response at all from the physical device. A
separate Node-RED flow over a different USB-RS485 adapter *does* get real
data back from the same device (`[17344,45523,17247,4399]`), so the device
itself is reachable and responsive — pointing at something specific to this
board's RS485 transceiver wiring/continuity (the same unresolved item flagged
at the top of this doc), not firmware. Also noticed: only `reg 0` is ever
polled even though the dashboard was configured with 4 registers for that
slave — worth checking whether the last config save actually persisted all
4, or only 1 landed. Cross-checking against the Node-RED flow's actual
serial-port/read-node config (baud/parity/slave-id/address/function-code) is
the next step, to rule out a parameter mismatch alongside the wiring
question.

**Built and flashed.** All items below are implemented in code and compile/flash clean:

- `main/modules/modbus_rtu_manager.h/.c` — new module, UART1 on GPIO14/15, hardware
  RS485 half-duplex mode driving DE (GPIO23) via RTS; nRE (GPIO24) tied permanently low
  (see "Hardware/driver decision" below — this deviates slightly from a literal DE/nRE
  toggle and is documented as a deliberate simplification).
- Sequential poll task using the vendored `espressif/esp-modbus` RTU **serial master**
  API (`mbc_master_*`) — no hand-rolled CRC16/framing needed, the library already has it.
- `Rs485Config`/`Rs485SlaveConfig` in `config.h/.c`, NVS namespace `rs485`, array-backed
  for up to `RS485_MAX_SLAVES = 20` slaves.
- `modbus_rtu` block added to `http_server_build_status_json()` → flows into REST
  `/api/status`, WebSocket push, and MQTT publish automatically (no per-channel code).
- `GET /api/rs485-config` / `POST /api/save-rs485-config` — reboot-to-apply, same
  pattern as every other config section.
- Dashboard "RS485 / Modbus" tab — live values table + bus settings + a **dynamic
  add/remove slave list** (not a fixed single-slave form), capped at 20 client-side to
  match `RS485_MAX_SLAVES`.

**Decisions made against the open items below:**
1. Transceiver part number — not re-verified independently; relied on the continuity
   test already noted below. Worth a physical double-check against the board schematic.
2. **Reboot-to-apply** chosen (matches every other config section).
3. **Scaled to 20 slaves** (up from an initial cap of 4) once the user confirmed a real
   deployment needs 15-20 PLCs on the bus — dashboard now has a proper add/remove list
   instead of a single hardcoded slave form.
4. Baud/parity/register map are per-slave configurable from the dashboard; default
   9600 8N1, holding registers, no fixed target machine yet.

### Scale-up pass (15-20 slaves, long-run / low-overhead)

Once told the real target was "at least 15, dynamically add up to 20, optimized for
long run, not heavy on the ESP32," went back through the single-slave implementation
and made these changes (all flashed, none of this is aspirational):

- **`RS485_MAX_SLAVES` 4 → 20.** RAM/NVS budget checked before raising it: ~2.2KB
  static RAM for the status array, ~150 NVS entries (~5KB) in the `rs485` namespace —
  comfortable inside the 24KB `nvs` partition.
- **`CONFIG_FMB_MASTER_TIMEOUT_MS_RESPOND` 3000ms → 300ms** (the Kconfig-enforced
  minimum — traced the constant through `mbconfig.h`/`mb_m.c`, confirmed it's
  compile-time only, no per-request override exists in the master API). At the old
  3000ms default, one dead slave would've stalled the entire sequential poll round for
  3 full seconds — fatal to "stays responsive" at 15-20 slaves.
- **WebSocket push batched to once per completed poll round**, not once per slave —
  `rtu_poll_task` previously called `http_server_push_state()` after every single
  slave's poll; at 15-20 slaves that meant that many redundant full-status
  re-serializations/broadcasts back to back.
- **RTU status snapshot moved from a stack array to heap alloc/free** in
  `http_server_build_status_json()` — at cap 20 that snapshot is ~2.2KB, and the
  function is called from multiple task stacks (httpd worker + the poll task itself);
  heap-allocating it keeps every caller's stack usage flat regardless of slave count.
- **httpd worker stack bumped 4096 → 6144** — caught during a safety review: raising
  the slave cap grew `Rs485Config` (stack-declared in `api_rs485_config_get_handler`)
  from ~200 bytes to ~950 bytes, tight on the default 4096-byte httpd task stack.
- **Fixed a real ghost-slave bug** found while wiring up dynamic add/remove:
  `save_rs485_config` only ever wrote the slots actually present in the posted array —
  shrinking the list (e.g. save 3 slaves, remove one, save 2) left a stale
  `enabled=true` NVS flag on slot 2 from the earlier save, which would've resurfaced as
  a phantom slave on the next boot. Fixed by clearing every slot beyond the new count
  on every save.
- Dashboard now has a real add/remove slave list (`+ Add Slave` / per-row `DEL`, capped
  at 20 client-side) plus inline advisory text on realistic bus timing (RS485 is
  half-duplex — one request at a time across *all* slaves, ~30-100ms per slave at 9600
  baud when healthy, so total round time scales with slave count; raise baud or space
  out poll intervals for larger slave counts).

**Not yet verified: live data over an actual RS485 bus.** Bench-tested with a
`pymodbus` RTU slave simulator on a laptop via USB-RS485 adapter — as of this note, the
ESP32 poll task runs (`Status: RUNNING` in the dashboard) but zero requests have reached
the simulator (`# read` stays at 0 in the simulator's own register-monitor page), which
points at the physical A/B wiring/continuity between the board and the test adapter, not
firmware. Re-test once that's confirmed with a multimeter continuity check.

### Hardware/driver decision (reasoned from esp-modbus source, not in the original spec)

`uart_set_pin()` + `uart_set_mode(UART_MODE_RS485_HALF_DUPLEX)` let the ESP32 UART
hardware auto-drive DE (GPIO23) exactly framed around each transmitted byte — avoids
manual GPIO-toggle races around `uart_wait_tx_done()`. GPIO24 (nRE) is configured as a
plain output and driven low once at init, left there permanently (receiver always
enabled) — the RS485 half-duplex hardware mode already suppresses the self-echo
internally, so a second toggled control line isn't needed. UART1 used (console is on
UART0, confirmed via `CONFIG_ESP_CONSOLE_UART_NUM=0`).

## Goal

Add RS485-based **Modbus RTU master** capability to PL Connect. The device polls one
or more Modbus RTU slave devices (sensors, meters, PLCs) over a wired RS485 bus and
exposes the parsed register values through the **same channels the existing I/O
already uses**: MQTT publish, REST API, WebSocket live push, and the web dashboard.

This is additive — it must not change existing DO/DI/Ethernet/Wi-Fi/MQTT/Modbus-TCP
behavior.

## Relationship to existing modules (read `memory/project_analysis.md`)

| Existing | New |
|---|---|
| Modbus **TCP slave** (device is polled by an external PLC/SCADA, read-only) | Modbus **RTU master** (device polls external slaves itself, read-only) |
| MQTT **publish-only**, built from `http_server_build_status_json()` | Same builder, extended with an `modbus_rtu` block |
| WebSocket `/ws` pushes full status snapshot on any change | Same mechanism — no new WS logic needed |
| `gpio.c` — background scan task + thread-safe getters + critical section | New module should follow the identical shape |

## Hardware

- RS485 transceiver already present on the carrier board (schematic: `U13`,
  `SN65HVD11DR` — confirm this part number matches your actual board revision).
- **GPIO map — already committed, do not reassign:**
  - Relay outputs (DO): GPIO 6, 20
  - Digital inputs (DI): GPIO 46, 53, 27, 47, 45, 1, 33, 54, 26, 48
  - Ethernet (internal EMAC + IP101 PHY): fixed by silicon, not reassignable
- **RS485 GPIOs — confirmed by continuity test (superseded, see below):**
  - GPIO14 = DI (data into the bus — wire to UART TX)
  - GPIO15 = RO (data out of the bus — wire to UART RX)
  - GPIO23 = DE (Driver Enable, HIGH to transmit)
  - GPIO24 = nRE (Receiver Enable, active LOW)
  - No collision with the DO/DI list above or with Ethernet's fixed pins.
  - **Superseded during real-hardware bring-up** (see "Real-hardware bring-up"
    section above): remapped to **GPIO37 (TX) / GPIO38 (RX) / GPIO5 (DE) /
    GPIO4 (nRE)** to free up 14/15/23/24 for another purpose. GPIO37/38 double
    as console UART0 — no serial console while RS485 uses these pins,
    confirmed as an acceptable tradeoff. If wiring hardware from this doc,
    use the new pins, not the ones listed just above.

## Protocol requirements

- Role: Modbus RTU **master** — this device always initiates the request; slaves
  never speak first.
- UART: configurable baud (default 9600), 8N1, matching the target slave device(s).
- CRC16 validated on every response; on CRC mismatch, timeout, or wrong
  slave-id/function-code in the reply, discard the frame silently (log via `ESP_LOGW`)
  — never crash or hang the bus.
- Per configured slave: slave ID, register area (holding/input), start address,
  register count, poll interval, optional label + scale factor (e.g. raw/10 → °C).
- Design the config storage as a small array from the start, even if the initial UI
  only exposes one entry — so adding more slaves later doesn't require a schema change.
- Read-only. Do not implement write function codes (06/16) — mirror the existing
  Modbus TCP slave's deliberate "read-only, master/slave can't be corrupted"
  philosophy.

## New module: `main/modules/modbus_rtu_manager.h` / `.c`

Follow the same shape as `gpio.c` / `modbus_manager.c`:

- `modbus_rtu_manager_init()` — called from `main.c` after `gpio_init()`, skipped
  (no `ESP_ERROR_CHECK`, no abort) if disabled in config, same failure-tolerance
  pattern as the existing `modbus_manager_init()`.
- Background FreeRTOS task polls each configured slave/register block at its
  interval; sequential polling on a shared bus (RS485 is half-duplex — only one
  outstanding request at a time across all configured slaves).
- Private storage (parsed values, last-good timestamp, online/stale flag per slave)
  guarded by a `portMUX` critical section, same pattern as `modbus_manager.c`'s
  `s_discrete`/`s_input` arrays — never expose raw pointers to callers.
- Thread-safe getter(s) for the HTTP/WS/MQTT layer to read current values.

## Integration points

- **Status JSON:** extend `http_server_build_status_json()` with a new key, e.g.
  `"modbus_rtu": [ { "id":1, "label":"...", "values":[...], "online":true,
  "last_update_ms":... }, ... ]`. Because REST `/api/status`, WebSocket push, and MQTT
  publish all already share this one builder, RS485 data flows into all three
  automatically — do not create separate serialization logic per channel.
- **REST endpoints** (follow existing naming/response conventions):
  - `GET /api/rs485-config` — bus settings + configured slave list
  - `POST /api/save-rs485-config` — save + apply
  - Decide: reboot to apply (matches existing WiFi/LAN/MQTT/Modbus-TCP pattern, less
    work) vs. apply live (nicer UX, more work) — existing project always reboots on
    config save, so default to that unless told otherwise.
- **NVS persistence:** new namespace (e.g. `rs485`) or extend `channels`, storing bus
  baud + slave/register list, following the existing `config.c` struct + load/save
  function pattern.

## Web dashboard

- New tab, e.g. **"RS485 / Modbus"**, alongside I/O Monitor / Channel Config /
  Network / System — same visual style as the existing tabs.
- Live values auto-update via the existing WebSocket `/ws` full-snapshot push — no
  new client-side polling logic needed, only new rendering for the `modbus_rtu` key.
- Config sub-section to add/edit slave entries (slave ID, register start/count,
  label, scale factor), matching the Channel Config tab's existing form style.
- Show per-slave online/stale indicator and last-updated time, same spirit as the
  DI table's live state column.

## Non-goals

- No Modbus RTU **slave** mode (this device is master-only on the RS485 side; the
  existing Modbus TCP slave is unrelated and unaffected).
- No writes to remote slave registers.
- No changes to existing DO/DI GPIO assignments or Ethernet/Wi-Fi behavior.

## Acceptance criteria

- [x] Device polls all configured Modbus RTU slaves over RS485 at their configured
      intervals without blocking the DI scan (50 ms), WebSocket, or MQTT tasks. —
      poll task is independent FreeRTOS task, same priority tier as the existing
      Modbus-TCP sync task.
- [x] CRC / slave-id / function-code mismatches are discarded without crashing or
      hanging the bus for subsequent polls. — surfaced as `esp_err_t` by
      `mbc_master_send_request()`, logged via `ESP_LOGW` (moved outside the critical
      section after an initial review caught it there), never `ESP_ERROR_CHECK`'d.
- [x] Parsed values appear in `/api/status`, WebSocket push, and MQTT publish JSON
      under `modbus_rtu`. — same shared builder as everything else, code-verified.
- [x] Web dashboard shows live values with auto-refresh via the existing WebSocket,
      no page reload. — new tab wired into `applyState()`/`renderModbusRtu()`.
- [x] Bus + slave config is editable from the dashboard and persists across reboot. —
      NVS namespace `rs485`, reboot-to-apply.
- [x] No regression to existing DO/DI/Ethernet/Wi-Fi/MQTT/Modbus-TCP functionality. —
      purely additive; feature is no-op when disabled (the shipped default).
- [x] Scales to 15-20 slaves, dynamically addable, without getting heavy on the ESP32 —
      see "Scale-up pass" above (raised cap, lowered master timeout, batched WS push,
      heap-allocated snapshot, bumped httpd stack). Code-level: done. Bus-level: still
      only bench-tested with a single simulated slave (see below), not a real 15-20 PLC
      chain — the design should hold given the physical/protocol math, but hasn't been
      soak-tested at that scale yet.
- [ ] **End-to-end over a real RS485 bus with data actually landing** — not yet
      confirmed; see "Not yet verified" above. This is the one item still open.

## Open items to confirm before/while implementing

1. Confirm RS485 transceiver part number on your actual board revision.
2. Reboot-to-apply vs. live-apply for RS485 config changes.
3. How many Modbus RTU slaves need to be supported on the bus at once — just one to
   start, or several?
4. Target baud rate / parity / register map, once the real machine is known.
