# Raspberry Pi 4 ↔ ESP32-P4 GPIO Reference

Combined GPIO reference for the **Raspberry Pi 4** (40-pin header) and the **Waveshare ESP32-P4-Module-DEV-KIT** (40-pin header), plus wiring guidance if you're interfacing the two boards directly. See the companion diagram: `rpi4_esp32p4_gpio_reference.svg`.

**Logic level:** both boards run 3.3V I/O. Neither the Pi's GPIOs nor the ESP32-P4's GPIOs are 5V-tolerant — never feed a 5V pin or a 5V signal into either board's GPIO header.

---

## Raspberry Pi 4 — 40-pin header

| Pin | Signal | Pin | Signal |
|----:|--------|----:|--------|
| 1 | 3.3V PWR | 2 | 5V PWR |
| 3 | GPIO2 (I2C1 SDA) | 4 | 5V PWR |
| 5 | GPIO3 (I2C1 SCL) | 6 | GND |
| 7 | GPIO4 | 8 | GPIO14 (UART0 TXD) |
| 9 | GND | 10 | GPIO15 (UART0 RXD) |
| 11 | GPIO17 | 12 | GPIO18 |
| 13 | GPIO27 | 14 | GND |
| 15 | GPIO22 | 16 | GPIO23 |
| 17 | 3.3V PWR | 18 | GPIO24 |
| 19 | GPIO10 (SPI0 MOSI) | 20 | GND |
| 21 | GPIO9 (SPI0 MISO) | 22 | GPIO25 |
| 23 | GPIO11 (SPI0 SCLK) | 24 | GPIO8 (SPI0 CE0) |
| 25 | GND | 26 | GPIO7 (SPI0 CE1) |
| 27 | Reserved (ID_SD — HAT EEPROM) | 28 | Reserved (ID_SC — HAT EEPROM) |
| 29 | GPIO5 | 30 | GND |
| 31 | GPIO6 | 32 | GPIO12 |
| 33 | GPIO13 | 34 | GND |
| 35 | GPIO19 (SPI1 MISO) | 36 | GPIO16 (SPI1 CE0) |
| 37 | GPIO26 | 38 | GPIO20 (SPI1 MOSI) |
| 39 | GND | 40 | GPIO21 (SPI1 SCLK) |

Pins 27/28 are reserved for HAT ID EEPROM auto-detection — avoid using them as general I/O even though they're wired to GPIO0/GPIO1 internally.

---

## ESP32-P4-Module-DEV-KIT — 40-pin header

| Pin† | Signal | Pin† | Signal |
|----:|--------|----:|--------|
| 1 | 3V3 | 2 | 5V |
| 3 | GPIO7 (I2C SDA) | 4 | 5V |
| 5 | GPIO8 (I2C SCL) | 6 | GND |
| 7 | GPIO23 | 8 | GPIO37 (UART TXD) |
| 9 | GND | 10 | GPIO38 (UART RXD) |
| 11 | GPIO21 | 12 | GPIO22 |
| 13 | GPIO20 | 14 | GND |
| 15 | GPIO6 | 16 | GPIO5 |
| 17 | 3V3 | 18 | GPIO4 |
| 19 | GPIO3 | 20 | GND |
| 21 | GPIO2 | 22 | GPIO1 |
| 23 | GPIO0 | 24 | GPIO36 |
| 25 | GND | 26 | GPIO32 |
| 27 | GPIO24 | 28 | GPIO25 |
| 29 | GPIO33 | 30 | GND |
| 31 | GPIO26 | 32 | GPIO54 |
| 33 | GPIO48 | 34 | GND |
| 35 | GPIO53 | 36 | GPIO46 |
| 37 | GPIO47 | 38 | GPIO27 |
| 39 | GND | 40 | GPIO45 |

**† Pin numbers are my addition**, not Waveshare's — their pinout diagram doesn't number the header. I applied the standard 2×20 convention (1 = top-left, 2 = top-right, alternating down the two columns) to match the Pi table above. Cross-check against your board's silkscreen if it has printed numbers.

---

## Bus cross-reference

| Bus | Raspberry Pi 4 | ESP32-P4-Module-DEV-KIT |
|---|---|---|
| **I2C** | SDA = GPIO2 (pin 3), SCL = GPIO3 (pin 5) — I2C1, default fixed-function pins | SDA = GPIO7 (pin 3), SCL = GPIO8 (pin 5) — routed through the GPIO matrix, so reassignable in firmware |
| **UART** | TXD = GPIO14 (pin 8), RXD = GPIO15 (pin 10) — UART0 | TXD = GPIO37 (pin 8), RXD = GPIO38 (pin 10) — also GPIO-matrix routed |
| **SPI** | SPI0: MOSI=GPIO10(19), MISO=GPIO9(21), SCLK=GPIO11(23), CE0=GPIO8(24), CE1=GPIO7(26). SPI1: MISO=GPIO19(35), CE0=GPIO16(36), MOSI=GPIO20(38), SCLK=GPIO21(40) | Not broken out as dedicated SPI on this header — any GPIO can be mapped to SPI via the GPIO matrix if you need it |
| **Power** | 3.3V ×2 (pins 1, 17), 5V ×2 (pins 2, 4) | 3.3V ×2 (pins 1, 17), 5V ×2 (pins 2, 4) |
| **GND** | 8 pins: 6, 9, 14, 20, 25, 30, 34, 39 | 8 pins: 6, 9, 14, 20, 25, 30, 34, 39 |

Worth noting: the power and GND pins land in **exactly the same positions** on both headers. That's likely deliberate on Waveshare's part for physical/electrical compatibility with the Pi's 40-pin form factor (e.g. accepting Pi HATs) — worth confirming on Waveshare's product page if that matters for your mechanical design.

---

## Interfacing the two boards directly

If you're wiring these together rather than just using each pinout as a standalone reference, here are the two most common patterns. I don't know your exact use case for this pairing, so treat these as starting points and tell me the actual role split (e.g. which board talks Modbus, which one's the network/MQTT side) if you want a wiring scheme tailored to it.

### UART bridge

| Signal | Pi Pin | | ESP32-P4 Pin | Signal |
|---|:-:|:-:|:-:|---|
| GND | 6 | ↔ | 6 | GND |
| UART0 TXD (GPIO14) | 8 | ↔ | 10 | GPIO38 (RXD) |
| UART0 RXD (GPIO15) | 10 | ↔ | 8 | GPIO37 (TXD) |

Cross TX↔RX — never wire TX to TX. Both sides are 3.3V logic, so no level shifter is needed.

### I2C bridge

| Signal | Pi Pin | | ESP32-P4 Pin | Signal |
|---|:-:|:-:|:-:|---|
| GND | any | ↔ | any | GND |
| SDA | 3 | ↔ | 3 | SDA |
| SCL | 5 | ↔ | 5 | SCL |

The Pi has onboard I2C pull-ups on pins 3/5. If the ESP32-P4 side also has its own pull-ups populated, you'll get two sets in parallel — usually harmless at short bus lengths, but if you see bus errors, remove one side's. Also settle which board is the I2C master before wiring; the simple case only supports one.

### Always tie a ground first

Whichever signals you use, connect a GND pin on each board before anything else — same GND set on both (6, 9, 14, 20, 25, 30, 34, 39).

### Before adding anything new to the ESP32-P4

A subset of ESP32-P4 GPIOs are strapping pins that set boot mode at reset — pulling one high/low externally can stop the board from booting. I don't have the P4's strapping-pin table memorized reliably enough to give you exact numbers, so check Espressif's ESP32-P4 datasheet before adding any external pull-up/pull-down to a GPIO you haven't used before, especially in the low-numbered range.

---

## Notes & assumptions

- Both pinouts are transcribed directly from the images you shared.
- RPi4's I2C1/UART0/SPI0/SPI1 assignments are the default fixed-function pins; some can be relocated via device-tree overlay if you need those physical pins for something else.
- ESP32-P4 peripherals route through a GPIO matrix, so the SDA/SCL/TXD/RXD pins here are this dev kit's default assignment, not a silicon-fixed pin — reassignable in firmware.
- I'd sanity-check anything safety- or timing-critical (especially strapping pins) against Espressif's ESP32-P4 datasheet before finalizing hardware — the P4 is recent enough that I'd rather flag the gap than guess.
