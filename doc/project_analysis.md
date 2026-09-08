# ESP32-P4 I/O WebServer ("PL Connect") — Full Project Analysis

_Last updated: 2026-09-06 — RS485/Modbus RTU master registers gained Coil (FC01) /
Discrete Input (FC02) areas alongside the existing Holding/Input, plus a `BIT`
data_type (single 0.0/1.0 value, forced whenever area=Coil/Discrete); see
`RS485_MODBUS_RTU_CONTEX.md`'s "Coil + Discrete Input register support" entry.
Version bumped to 1.1.1. Earlier: RTU dashboard values fixed to 2 decimals +
per-register display unit added (`Rs485RegisterConfig.unit`, cosmetic, up to 11
chars, e.g. `120.30V`); nav tabs reordered (I/O Monitor · Channel Config · Network ·
RS485 · System) and "RS485 / Modbus" tab renamed to "RS485"; `/update` OTA page
password-gated (`X-OTA-Password` header, hardcoded `"123456"`, checked server-side
before OTA begins). Earlier still: RS485/Modbus RTU master registers made dynamic
per slave (up to 16/slave, see below); added an I2C SSD1306 OLED (SDA=GPIO2,
SCL=GPIO3) showing the device's Ethernet IP_

## Purpose
ESP-IDF firmware for an ESP32-P4 industrial I/O module. Hosts an embedded web control
panel for 2 relay outputs and 10 digital inputs over Ethernet (+ optional Wi-Fi), with
live WebSocket updates, MQTT state publishing, and over-the-air firmware update. The
entire web UI is compiled into flash — no external filesystem.

Firmware: **PL Connect v1.1.1** (`main/version.h`).

---

## File Structure

```
esp32_io_webserver/
├── CMakeLists.txt              # Root ESP-IDF build config
├── partitions.csv             # 16MB flash, dual-OTA, 3MB per app slot
├── sdkconfig                  # 16MB flash + custom partition table + WS + MQTT enabled
├── esp32_io_webserver.ino     # Original Arduino sketch (backup only, NOT built)
├── flash.sh                   # Linux one-shot build + flash + monitor
├── SETUP_STEPS.md             # ESP-IDF v5.5.3 setup + build/flash procedure
├── memory/project_analysis.md # This file
├── main/
│   ├── CMakeLists.txt
│   ├── version.h              # FIRMWARE_VERSION / APP_NAME / COPYRIGHT
│   ├── main.c                 # app_main startup orchestration (~45 lines)
│   ├── http_server.c          # HTTP + REST + WebSocket + OTA (~790 lines)
│   ├── modules/
│   │   ├── config.h / .c          # NVS config: network + MQTT + per-channel
│   │   ├── gpio.h / .c            # relay/DI driver, scan task, counters
│   │   ├── network.h / .c         # Ethernet (IP101) + optional static DNS
│   │   ├── wifi_manager.h / .c    # Wi-Fi STA via esp_wifi_remote + optional DNS
│   │   ├── mqtt_manager.h / .c    # MQTT publish-only client
│   │   ├── modbus_manager.h / .c  # Modbus TCP slave (read-only monitoring)
│   │   ├── modbus_rtu_manager.h / .c  # Modbus RTU master over RS485 (read-only polling)
│   │   ├── oled_display.h / .c    # I2C SSD1306 OLED — shows device IP
│   │   └── http_server.h          # HTTP public API (incl. status-JSON builder)
│   └── ui/
│       ├── index_html.h/.cpp      # SPA shell + extern decls
│       ├── style_css.cpp          # CSS
│       ├── app_js.cpp             # front-end (WebSocket, tabs, forms)
│       └── page_{io,config,network,system,rs485}_html.cpp  # lazy page fragments
```

---

## Framework & Build

| Item | Detail |
|------|--------|
| Framework | ESP-IDF **v5.5.3** (required for P4 rev v3.1 bootloader) |
| Target MCU | **ESP32-P4** silicon rev v3.1, 16 MB flash, 400 MHz dual-core |
| Wi-Fi | ESP32-C6 co-processor via `esp_hosted` / SDIO (`esp_wifi_remote`) |
| Ethernet | internal EMAC + **IP101** PHY |
| RTOS / TCP-IP / HTTP | FreeRTOS / lwIP / esp_http_server |
| Libraries | cJSON, NVS, esp_eth, esp_wifi_remote, esp-mqtt, esp-modbus, esp_lcd (SSD1306), app_update (OTA) |
| Build | `. ~/esp/esp-idf/export.sh && idf.py build` |
| Flash | `idf.py -p /dev/ttyACM0 flash` (board = `/dev/ttyACM0` on this PC) |
| Env details | see memory `idf-build-env.md` |

---

## Initialization Flow (`main.c`)

```
app_main()
  ├─ nvs_flash_init
  ├─ load_config              (config.c — "system" NVS ns)
  ├─ gpio_init                (DO 6,20 ; DI 46,53,27,47,45,1,33,54,26,48)
  ├─ gpio_start_input_scan    (scan task 50ms + counter-save task 10s)
  ├─ network_init             (Ethernet static/DHCP + optional static DNS)
  ├─ wifi_manager_init        (Wi-Fi STA if enabled, else skip)
  ├─ http_server_start        (port 80: REST + WebSocket + OTA)
  ├─ mqtt_manager_init        (MQTT publish-only if enabled, else skip)
  ├─ modbus_manager_init      (Modbus TCP slave if enabled, else skip; no ESP_ERROR_CHECK)
  ├─ modbus_rtu_manager_init  (RS485 Modbus RTU master if enabled, else skip; no ESP_ERROR_CHECK)
  ├─ oled_display_init        (I2C SSD1306, shows device IP; logs + skips on failure, no ESP_ERROR_CHECK)
  ├─ apply saved DI modes + invert flags
  └─ esp_ota_mark_app_valid_cancel_rollback   (anti-rollback)
```

---

## Hardware Pin Map

| Function | GPIO | Count |
|----------|------|-------|
| Relay outputs (DO) | 6, 20 | 2 |
| Digital inputs (DI) | 46, 53, 27, 47, 45, 1, 33, 54, 26, 48 | 10 |
| Ethernet | internal EMAC + IP101 PHY | — |
| RS485 (UART1) | TX=37, RX=38, DE=5 (RTS, hw-driven), nRE=4 (tied low) | — |
| OLED (I2C) | SDA=7, SCL=8 | — |

⚠ RS485 TX/RX (GPIO37/38) double as the console UART0 pins — no serial console
while RS485 uses these pins. Deliberate tradeoff: the original pins (14/15/23/24)
needed freeing up for another purpose.

- Relays: `GPIO_MODE_OUTPUT`.
- Inputs: `GPIO_MODE_INPUT` + internal pull-up; physical LOW = logical HIGH,
  XOR with per-channel invert flag.

---

## HTTP Layer (`http_server.c`)

- **SPA** served from `/`; fragments lazy-loaded from `/page/{io,config,network,system}`.
- **WebSocket `/ws`** — live state push. GPIO change → `gpio` change callback →
  `http_server_push_state` → async frame to all WS clients (no polling).
- `http_server_build_status_json()` is the shared status builder (used by REST, WS, and
  MQTT). Payload: relays[], inputs[], counts[], modbus_rtu[], ip, wifi_ip,
  wifi_connected, version, copyright.
- `max_uri_handlers = 28`, `recv_wait_timeout = 30 s` (for large OTA uploads). ~25
  handlers registered after adding RS485 (still under the cap). `stack_size = 6144`
  (bumped from the esp_http_server default of 4096, headroom for building nested
  RS485 config/status JSON across up to 20 slaves). `Rs485Config` itself is
  ~12KB now that each slave embeds a 16-register array — heap-allocated (not
  stack-declared) in both `api_rs485_config_get_handler` and `main.c`'s boot
  sequence; a stack-declared copy would have overflowed `main.c`'s 3584-byte
  main task stack immediately (caught during this pass, before flashing).

### REST endpoints
`GET /`, `/style.css`, `/app.js`, `/page/*`, `/api/status`, `/api/relay`,
`/api/channel-config`, `/api/network-config`, `/api/mqtt-config`,
`/api/modbus-config`, `/api/rs485-config`, `GET /api/reset-counter?ch=`,
`GET /favicon.ico`, `GET/POST /update` (OTA), `POST /api/save-channel-config`,
`/api/save-wifi-config`, `/api/save-lan-config`, `/api/save-mqtt-config`,
`/api/save-modbus-config`, `/api/save-rs485-config`, `/api/reboot`. Config saves reboot
via background task. `max_uri_handlers = 28`. `POST /update` is password-gated: the
client-side page `prompt()`s for a password and sends it as an `X-OTA-Password`
request header; `ota_update_post_handler` checks it against the hardcoded
`OTA_UPDATE_PASSWORD` (`"123456"`) before calling `esp_ota_begin`, rejecting with
401 on mismatch — plain-text over HTTP, a casual-push deterrent, not real auth
(no TLS anywhere on this device).

---

## Config Module (`config.c`)

`Config` struct (NVS "system" ns) — network + MQTT:
```c
char wifi_ssid[33]; char wifi_pass[65];
char wifi_ip[16]; char wifi_subnet[16]; char wifi_gateway[16]; char wifi_dns[16];
bool wifi_useStatic; bool wifi_enabled;
char lan_ip[16];  char lan_subnet[16];  char lan_gateway[16];  char lan_dns[16];
bool lan_useStatic;
bool mqtt_enabled; char mqtt_broker[64]; uint16_t mqtt_port;
char mqtt_topic[64]; uint16_t mqtt_interval;
bool mb_enabled; uint16_t mb_port; uint8_t mb_unit_id;   // Modbus TCP slave
```
- Defaults: LAN static `192.168.1.101` / gw `192.168.1.1` / mask `255.255.255.0`;
  Wi-Fi disabled; MQTT disabled, broker `broker.hivemq.com`:1883, topic `esp32/status`,
  interval 1000 ms. DNS fields default empty.
- `ChannelConfig` (NVS "channels" ns): per-channel label / mode / invert for DI & DO,
  plus DI counter values (`cnt_N`).
- Functions: `load_config`, `save_wifi_config`, `save_lan_config`, `save_mqtt_config`,
  `save_modbus_config`, `load_channel_config`, `save_channel_config`,
  `load_rs485_config`, `save_rs485_config`.
- Modbus TCP defaults: disabled, port 502, unit id 1 (NVS keys `mb_en`/`mb_port`/`mb_uid`).
- `Rs485Config` / `Rs485SlaveConfig` (NVS `rs485` ns): bus enable/baud/parity/data-bits/stop-bits +
  `Rs485SlaveConfig slaves[RS485_MAX_SLAVES]` (`RS485_MAX_SLAVES = 20`, raised from an
  initial 4 once the target deployment turned out to be 15-20 PLCs), each with
  enabled/slave_id/poll_interval_ms/label + a **dynamic per-slave register list**
  (`Rs485RegisterConfig regs[RS485_MAX_REGS_PER_SLAVE]`, `RS485_MAX_REGS_PER_SLAVE=16`)
  — a slave is no longer one flat register block; each register has its own
  reg_type (holding|input|**coil**|**discrete**, 2026-09-06)/start_addr/data_type
  (U16/S16/U32/S32/F32/**BIT** — 32-bit types span 2 words, big-endian word order
  assumed, unconfirmed against a real target device; BIT is a single 0.0/1.0 value,
  forced whenever reg_type is coil/discrete)/label/scale/**unit** (`char unit[RS485_UNIT_LEN=12]`,
  added 2026-09-02 — free-text display suffix, e.g. `V`/`A`/`kW`, purely
  cosmetic, appended to the value on the dashboard; no effect on decode/scale).
  Slave-level scalar keys unchanged pattern
  (`rtu_sid_%d`, `rtu_ival_%d`, `rtu_lbl_%d`, `rtu_en_%d`); the register array
  itself is stored as **one NVS blob per slave** (`rtu_rg_%d`, sized by
  `rtu_rc_%d`) rather than exploding into per-field indexed keys — the naive
  indexed-key approach would be ~1600 NVS entries (~51KB) at the 20-slave/
  16-register ceiling, blowing the 24KB `nvs` partition; the blob approach
  keeps worst case to ~12KB (adding `unit` grows this slightly but stays well
  under the ceiling). Defaults: disabled, 9600 8N1, one slave enabled with one
  default register (id 1, holding, addr 0, U16, scale 1.0, unit empty). Adding
  `unit` to the struct is itself a **breaking NVS schema change** for the blob
  (raw struct copy) — same precedent as the earlier 4→20 slave-cap change and
  the original dynamic-register-list change: RS485 config resets to defaults
  on first boot after this update.
  `save_rs485_config` clears the `rtu_en_%d` flag for every slot beyond the
  newly-saved count on each save — without this, shrinking the slave list
  (save 3, remove one, save 2) would leave a stale `enabled=true` flag on slot
  2 that resurfaces as a ghost slave on next load (a real bug caught while
  building the dynamic add/remove UI, fixed at the write site). This is a
  breaking NVS schema change for the `rs485` namespace only — RS485 config
  resets to defaults on first boot after this update (no migration written,
  same precedent as the earlier 4→20 slave-cap change).

---

## GPIO Module (`gpio.c`)

- `NUM_RELAYS = 2`, `NUM_INPUTS = 10`. DI modes: 0 Normal / 1 Counter / 2 Frequency.
  Normal and Counter are fully implemented in the scan task; Frequency is a reserved
  mode value with no runtime behavior yet.
- DO per-channel `mode` (Normal/Pulse/Timer) and `invert` are persisted via
  `ChannelConfig` but are informational only — the GPIO module has no setter for them
  and relay output is always direct on/off via `gpio_set_relay`. The web UI's "Pulse"
  action is a client-side timed sequence of `/api/relay` calls, not a firmware behavior.
- Scan task polls every 50 ms; counter increments on rising edge (Counter mode).
- Counters persist to NVS every 10 s when dirty; loaded at boot.
- Change callback fires on any DI/relay/counter change → drives WebSocket push.
- API: `gpio_init`, `gpio_set_relay`, `gpio_get_relay_states`, `gpio_get_input_states`,
  `gpio_get_counts`, `gpio_reset_count`, `gpio_set_di_mode`, `gpio_set_di_invert`,
  `gpio_set_change_callback`, `gpio_start_input_scan`.

---

## Network Module (`network.c`)

- Ethernet PHY **IP101**, internal EMAC.
- Static or DHCP per `lan_useStatic`.
- Optional static DNS: if `lan_dns` non-empty, applied as `ESP_NETIF_DNS_MAIN` via
  `esp_netif_set_dns_info`. Empty → rely on DHCP DNS / none.
- `network_get_ip()` returns current Ethernet IP string.

## Wi-Fi Module (`wifi_manager.c`)

- Wi-Fi STA over `esp_wifi_remote` (C6 co-processor). Skipped when disabled or SSID unset.
- Auto-reconnect on disconnect. Optional static IP + optional DNS (same pattern as LAN).
- `wifi_manager_is_connected()`, `wifi_manager_get_ip()`.

## MQTT Module (`mqtt_manager.c`)

- Publish-only, anonymous, plain TCP. Skipped when disabled or no broker.
- Connects `mqtt://<broker>:<port>`; background task publishes status JSON to
  `mqtt_topic` every `mqtt_interval` ms (min-clamped to 250 ms), QoS 0, no retain.
- Reuses `http_server_build_status_json()`. `mqtt_manager_is_connected()` reports state.
- Hostname broker requires working DNS (see network DNS above).

## Modbus Module (`modbus_manager.c`)

- Modbus **TCP slave**, **read-only** (no coils/holding regs → master cannot actuate).
  Skipped when disabled or no Ethernet netif. Uses `espressif/esp-modbus` v1.x
  (`mbcontroller.h`), bound to the netif from `network_get_netif()`.
- Register map: discrete inputs 0–9 = DI0..DI9, 10–11 = relay DO0/DO1; input regs 0–19 =
  10 DI counters (32-bit, 2 regs each, low word first).
- Owns private storage (`s_discrete[2]`, `s_input[20]`) sized to match descriptors (no OOB).
  A `mb_sync` task (prio 4, 4096 stack) copies GPIO state in every 50 ms via the
  thread-safe getters, guarded by a `portMUX` critical section (memory ops only inside).
- Crash-safe: every `esp_err_t` checked, `mbc_slave_destroy()` on failure, no
  `ESP_ERROR_CHECK` (module + main.c call site) → init failure never aborts boot.
  `modbus_manager_is_running()` reports state.

## RS485 RTU Master Module (`modbus_rtu_manager.c`)

- Modbus **RTU master** over RS485 — mirror role of `modbus_manager.c`: this device
  initiates every request and polls external slaves, rather than being polled. Skipped
  when `Rs485Config.enabled` is false (default). Uses the same
  `espressif/esp-modbus` component's serial-master API (`mbc_master_init/setup/start`,
  `MB_PORT_SERIAL_MASTER`) — no hand-rolled CRC16/RTU-framing, the library has it.
- UART1 on GPIO37 (TX) / GPIO38 (RX) — these double as the console UART0 pins;
  reassigning them to RS485 was a deliberate tradeoff (the original pins
  14/15/23/24 needed freeing up), and means no serial console while this pin
  assignment is active. Pins remapped via `uart_set_pin()` *after*
  `mbc_master_start()` (UART driver install happens inside `eMBMasterSerialInit`,
  called from `mbc_master_start()`, not `setup()` — traced through
  `mbc_serial_master.c` to confirm ordering). DE (GPIO5) is hardware-auto-driven
  via RTS; nRE (GPIO4) is a plain GPIO output tied low permanently (RS485
  half-duplex HW mode already suppresses the self-echo, so toggling nRE in sync
  isn't needed).
- Data bits (5-8) and stop bits (1/1.5/2) also applied post-`mbc_master_start()`,
  same pattern as the pin remap — `mb_communication_info_t` only exposes
  mode/port/baudrate/parity (traced through `esp_modbus_common.h`), and esp-modbus's
  own serial init hardcodes 8 data bits / 1 stop bit internally (traced through
  `freemodbus/port/portserial_m.c`), so both need `uart_set_word_length()`/
  `uart_set_stop_bits()` overrides to be configurable at all. **Must run before**
  `uart_set_mode(UART_MODE_RS485_HALF_DUPLEX)`, not after — doing it after hung the
  boot on real hardware with no error logged and no crash dump (confirmed via a
  live serial capture: boot log stopped dead right after `uart_driver_install`,
  and the final "RS485 Modbus RTU master up" line never printed). Reordering ahead
  of the mode switch fixed it — reconfiguring frame parameters while RS485
  half-duplex mode is already active (DE tied to TX framing) is the suspected
  trigger. Mark/Space parity is
  **not exposed** — the ESP32 UART peripheral's `uart_parity_t` only has
  None/Even/Odd, no hardware mode for a fixed-value parity bit. Flow control is
  **not exposed either** — RS485 half-duplex mode already commits RTS to driving DE,
  so hardware RTS/CTS can't coexist with it, and Modbus RTU doesn't use flow control
  at the protocol level regardless.
- Single `rtu_poll_task` (prio 4, 4096 stack) iterates configured slaves each tick,
  checks per-slave elapsed time vs `poll_interval_ms` (`esp_timer_get_time()`), and
  when a slave is due, loops over its **dynamic per-slave register list**
  (`reg_count` entries, ≤ `RS485_MAX_REGS_PER_SLAVE=16`) issuing one
  `mbc_master_send_request()` per register — this is one level deeper than the old
  one-request-per-slave design, but still satisfies "only one outstanding request on
  the half-duplex bus" since it's the same sequential loop, no extra bus-arbitration
  lock needed. Word count per request is 1 (U16/S16) or 2 (U32/S32/F32) from the
  register's `data_type`; multi-word values are decoded assuming big-endian word order
  (standard Modbus convention, unconfirmed against a real target device). Function
  code is raw Modbus (`0x03` holding / `0x04` input) via `mb_param_request_t`, not the
  cid/descriptor-table abstraction (unnecessary for flat register reads). A slave's
  round is aggregate pass/fail — all its registers must succeed to mark `online=true`
  and update `values[]`; any register failure marks the whole round failed for
  staleness purposes (simpler than partial-per-register online tracking). WS push
  (`http_server_push_state()`) is batched to **once per completed poll tick** via a
  `polled_any` flag, not once per slave/register — matters once total register count
  across 15-20 slaves scaled up (was one redundant full-status broadcast per slave
  before, now would be even more per-register without the batch).
- Private storage: `RtuSlaveStatus s_status[RS485_MAX_SLAVES]` (parsed
  `float values[RS485_MAX_REGS_PER_SLAVE]`, matching `reg_labels[][]` and (added
  2026-09-02) `reg_units[][RS485_UNIT_LEN]` copied in at init from
  `Rs485RegisterConfig.unit` so the status snapshot is self-describing, `online`,
  `last_update_ms`), guarded by a `portMUX` critical section — mirrors
  `modbus_manager.c`'s `s_discrete`/`s_input` pattern. `ESP_LOGW` for poll failures
  is called **outside** the critical section (an
  early draft had it inside, which is an anti-pattern — logging/blocking calls inside a
  critical section risk long interrupts-disabled windows → possible watchdog trip;
  caught and fixed before shipping).
- On error (CRC/timeout/wrong slave-id or function-code — all surfaced as `esp_err_t`
  by `mbc_master_send_request`), logs and keeps going; marks a slave `online=false` if
  stale beyond 3× its poll interval. Never crashes or hangs the bus for later polls.
  `CONFIG_FMB_MASTER_TIMEOUT_MS_RESPOND` lowered from the 3000ms default to **300ms**
  (the Kconfig-enforced floor — traced through `mbconfig.h`/`mb_m.c`, confirmed it's
  compile-time only, no per-request override in the master API) so one dead
  slave/register can't stall a 15-20-slave poll round for seconds — now more relevant
  since a round can be many requests deep (up to 16 registers per slave).
- `modbus_rtu_manager_get_status()` — thread-safe snapshot copy for the HTTP layer
  (`RtuSlaveStatus out[RS485_MAX_SLAVES]` + configured count), never exposes raw
  pointers. `modbus_rtu_manager_is_running()` reports state. The caller
  (`http_server_build_status_json()`) heap-allocates the `out` buffer rather than
  stack-declaring it — at `RS485_MAX_SLAVES=20` that's ~2.2KB, and this function is
  called from both the httpd worker task and `rtu_poll_task` itself; heap keeps stack
  usage flat regardless of slave count.
- Crash-safe init: every esp-modbus/UART call checked, `mbc_master_destroy()` +
  return on failure, no `ESP_ERROR_CHECK` — same failure-tolerance as
  `modbus_manager_init()`.

---

## OLED Display Module (`oled_display.c`)

- Small I2C SSD1306 128x64 OLED (addr 0x3C) showing the device's current
  Ethernet IP address — always-on hardware peripheral, no enable toggle/NVS
  config (unlike MQTT/Modbus/RS485, which are optional soft-features; this
  mirrors GPIO/network in being unconditionally initialized).
- Uses ESP-IDF's built-in `esp_lcd` component (`esp_lcd_new_panel_io_i2c` +
  `esp_lcd_new_panel_ssd1306`) over the new `i2c_master` driver — no
  hand-rolled SSD1306 command sequence, no LVGL (unnecessary for a single
  line of digits that changes rarely; would add a flush-callback/tick-timer/
  task-lock footprint disproportionate to the need).
- GPIO2=SDA, GPIO3=SCL — checked against ESP32-P4's actual EMAC RMII pin
  assignment (`ETH_ESP32_EMAC_DEFAULT_CONFIG()`: MDC=31, MDIO=52, REF_CLK=50,
  TX_EN=49, TXD0=34, TXD1=35, CRS_DV=28, RXD0=29, RXD1=30, traced through
  `esp_eth_mac_esp.h`) and the existing DO/DI/RS485 map before wiring — no
  collision.
- Text rendered with a **self-authored 5x7 dot-matrix digit font** (digits
  0-9 and '.' only — that's all an IP string needs), derived column-by-column
  from the standard 7-segment on/off table rather than transcribed from any
  existing font asset (avoids the transcription-accuracy risk of copying a
  licensed bitmap font from memory). One byte per column, bit0 = top row of
  the page — matches the SSD1306's native page-addressed GDDRAM layout that
  `esp_lcd_panel_draw_bitmap()` writes through directly.
- Single background task polls `network_get_ip()` every 2s; only redraws
  (full-frame `esp_lcd_panel_draw_bitmap`) when the IP string actually
  changed, to avoid needless I2C traffic. No IP yet (netif up, DHCP/static
  not resolved) just shows "0.0.0.0" — no separate "connecting" text, since
  that would need letter glyphs the minimal font doesn't have.
- Crash-safe init: every i2c/esp_lcd call checked, cleans up
  (`i2c_del_master_bus`/`esp_lcd_panel_io_del`) and returns on failure, no
  `ESP_ERROR_CHECK` — same failure-tolerance pattern as every other
  peripheral module.

---

## Connection Capacity

- HTTP server: `max_uri_handlers = 28` (24 in use), `max_open_sockets` derives from
  `CONFIG_LWIP_MAX_SOCKETS` (10 in this build) minus a small reserve — shared across the
  HTTP server, WebSocket, MQTT client, and Modbus TCP slave.
- All JSON API responses (`send_json()` in `http_server.c`) send `Connection: close`, so
  each REST call releases its socket immediately rather than holding it keep-alive. The
  web UI also loads its Network/MQTT/Modbus config sequentially on page load and on
  switching to the Network tab (`await`ed one at a time in `app_js.cpp`) instead of
  firing all three concurrently — keeps the client's own footprint on the socket pool low
  right as the WebSocket upgrade is also being attempted.
- Modbus TCP slave: up to `CONFIG_FMB_TCP_PORT_MAX_CONN` concurrent masters (default 5),
  each connection idle-timed-out after `CONFIG_FMB_TCP_CONNECTION_TOUT_SEC` (default 20 s).
  External masters should hold one persistent connection and reuse it per poll rather
  than reconnecting every cycle.

## Persistence Summary (NVS)

- `system` ns: Wi-Fi + LAN (IP/subnet/gw/**dns**/static/enable, SSID/pass) + MQTT config.
- `channels` ns: per-channel labels/modes/invert + DI counters.
- `rs485` ns: bus enable/baud/parity/data-bits/stop-bits + per-slave config array (indexed keys).

---

## Partition Table (`partitions.csv`, 16 MB)

| Partition | Type | Offset | Size |
|-----------|------|--------|------|
| nvs | data/nvs | 0x9000 | 0x6000 |
| otadata | data/ota | 0xf000 | 0x2000 |
| phy_init | data/phy | 0x11000 | 0x1000 |
| ota_0 | app | 0x20000 | 0x300000 (3 MB) |
| ota_1 | app | 0x320000 | 0x300000 (3 MB) |

Firmware binary ≈ 1.05 MB → ~65% free per slot.

---

## Web UI (embedded in flash)

- **Tabs:** I/O Monitor · Channel Config · Network · RS485 · System (RS485 tab
  renamed from "RS485 / Modbus" and moved before System, 2026-09-02).
- Live updates via WebSocket (`connectWS`, auto-reconnect w/ backoff). Each push is a
  full status snapshot (same schema as `GET /api/status`), not a per-channel delta.
  Network/MQTT/Modbus config loads are sequenced (`await`ed one at a time) rather than
  fired concurrently, so they don't compete with the WS upgrade for the socket pool.
- System tab's free-heap and RSSI figures are illustrative placeholders, not live device
  telemetry. "Factory Reset" / "Export Config JSON" buttons are placeholders reserved for
  future wiring. The I/O Monitor's DI "SIM" control simulates a transition in the UI only
  (for demonstration), independent of actual GPIO state.
- Network tab forms: Wi-Fi, LAN, MQTT, Modbus TCP. Wi-Fi/LAN static sections include an
  optional **DNS** field. MQTT has enable toggle, broker, port, status topic, interval,
  and live connection status. Modbus has enable toggle, port, unit id, running status,
  and a read-only register-map legend.
- RS485 tab: live-values table (`rtu-rows`, one row per register from
  `data.modbus_rtu[].registers[]` via WS, no new polling logic; each value
  rendered as `Number(r.value).toFixed(2) + r.unit` e.g. `120.30V`, 2026-09-02)
  + bus settings
  (enable/baud/parity/data-bits/stop-bits/running status) + a **two-level dynamic list**:
  `rtuSlaves` JS array (`{slave_id,label,poll_interval_ms,registers:[]}`),
  each rendered as a `.net-form` card (`renderRtuCfgRows`/`addRtuSlaveRow`/
  `removeRtuSlaveRow`, capped at `RTU_MAX_SLAVES=20`) containing its own
  nested register sub-table (`renderRtuRegisterRows`/`addRtuRegisterRow`/
  `removeRtuRegisterRow`, capped at `RTU_MAX_REGS=16` per slave; columns
  Label/Area/Start Addr/**Unit**/Type/Scale — `unit` text input added
  immediately right of Start Addr, 2026-09-02) — reuses
  `.cfg-table`/`.net-form`/`.cfg-input`/`.cfg-select`/`.save-btn`/
  `.di-reset-btn` styling already defined for other tabs, no new CSS needed.
  Inline help text notes realistic bus timing now scales with total register
  count, not slave count (half-duplex, one request per register, one at a
  time).
- Key JS: `setDO`, `applyState`, `connectWS`, `loadChannelConfig`/`saveChannelConfig`,
  `loadNetworkConfig`/`saveWifiConfig`/`saveLanConfig`, `loadMqttConfig`/`saveMqttConfig`,
  `loadModbusConfig`/`saveModbusConfig`, `loadRs485Config`/`saveRs485Config`/
  `renderModbusRtu`/`renderRtuCfgRows`/`renderRtuRegisterRows`/`addRtuSlaveRow`/
  `removeRtuSlaveRow`/`addRtuRegisterRow`/`removeRtuRegisterRow`,
  `resetCounter`, `openPulse`/`runPulse`, `rebootDevice`.

---

## Key Design Patterns

1. Embedded web assets (C string literals) — no SPIFFS.
2. Event-driven HTTP + WebSocket push (no client polling).
3. FreeRTOS tasks: DI scan (50 ms), counter save (10 s), MQTT publish (interval),
   Modbus TCP sync (50 ms), Modbus RTU poll (per-slave interval, sequential on the bus).
4. cJSON for all serialization/parsing.
5. NVS persistence across reboots (config + counters).
6. Reboot-on-config-change via background task.
7. Dual-OTA with anti-rollback for safe field updates.
8. Modular C: config / gpio / network / wifi / mqtt / modbus / modbus_rtu / http_server
   self-contained.
9. Read-only-by-design mirrored on both Modbus roles: TCP slave has no writable areas,
   RTU master implements no write function codes (06/16) — neither role can be used to
   actuate anything, in either direction.

---

## Known Gaps / Notes
- Modbus TCP: read-only slave (monitoring); no writable areas, master cannot control outputs.
- Modbus RTU (RS485): read-only master (polling only); no write function codes
  implemented. Scaled to `RS485_MAX_SLAVES=20` with a dynamic add/remove dashboard UI,
  batched WS push, heap-allocated status snapshot, and a lowered master response
  timeout (300ms) — all code-level changes for the 15-20 slave target, not yet
  soak-tested at that scale on real hardware. Registers are now dynamic per slave too
  (up to `RS485_MAX_REGS_PER_SLAVE=16`, each with its own address/type/data-type/
  scale/label instead of one flat block) — 32-bit types (U32/S32/F32) assume
  big-endian word order, unconfirmed against a real target device. Went from one
  `mbc_master_send_request()` per slave to one per register (still sequential/
  half-duplex-safe); worth watching poll-round timing once soak-tested at 15-20
  slaves x several registers each. **Not yet verified end-to-end on a real bus at
  all** — bench test with a `pymodbus` RTU slave simulator over a USB-RS485 adapter
  shows the poll task running but zero requests reaching the simulator, pointing at
  physical A/B wiring/continuity between the board and the test rig rather than
  firmware. See `RS485_MODBUS_RTU_CONTEX.md`'s "Implementation status" section for
  the live thread.
- MQTT: publish-only (no subscribe/command topics), no auth, no TLS.
- `esp32_io_webserver.ino`: Arduino backup, not built.
- OLED display: shows Ethernet IP only, digits/'.' only (no letters) — a
  minimal-font tradeoff, not yet hardware-verified (no physical SSD1306
  wired up during this pass; init/task logic builds clean and follows the
  established `esp_lcd` reference example, but actual on-screen legibility
  hasn't been visually confirmed).
