# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Developer Profile

- 7+ years of professional experience in Python and embedded firmware development for Raspberry Pi platforms.
- Comfortable with hardware interfacing (GPIO, I2C, SPI, UART/serial), real-time constraints, and long-running unattended deployments.
- Expects senior-level engineering judgment in code and suggestions — skip beginner explanations of Python or basic embedded concepts.

## Non-Negotiable Rules

1. **Always update the version file whenever code changes.**
   - Location: `app/state.py::FIRMWARE_VERSION` — the only version string in
     this repo; it flows into the dashboard footer and every `/api/status`/
     WebSocket/MQTT payload. Mirror the bump into `doc/PI_PORT_REFERENCE.md`
     §1 and its `/api/status` example JSON in the same change (both cite the
     current version literally).
   - Follow Semantic Versioning (`MAJOR.MINOR.PATCH`):
     - `PATCH` — bug fixes, no behavior change
     - `MINOR` — new functionality, backward-compatible
     - `MAJOR` — breaking changes
   - The version bump must land in the *same* change as the code it describes. Never leave it stale, and never batch version bumps for later.

2. **Code must be production-optimized, not demo-quality.**
   - No placeholder/TODO logic in delivered output unless explicitly asked for.
   - Prefer explicit, typed, defensive code over clever-but-fragile shortcuts.
   - Every function that touches hardware, I/O, or external input should have a clear failure path — silent failure is not acceptable on deployed firmware.

3. **Firmware must be reliable by default.**
   - Assume the device can lose power, lose network, or run unattended for months — design for recovery, not just the happy path.
   - Validate all external inputs (sensor data, serial/UART payloads, network messages) before acting on them.
   - Avoid busy-waiting; use event-driven, interrupt-based, or properly scheduled patterns for I/O.
   - Use watchdog / auto-recovery patterns for long-running processes where appropriate.
   - Log meaningfully and consistently — this needs to be debuggable on a headless Pi in the field, not just on a dev machine.

## Coding Standards

- **Style:** PEP 8. Type hints on all function signatures. Docstrings on public functions/classes.
- **Dependencies:** Keep minimal — Pi deployments often run on constrained storage/CPU. Justify any new dependency before adding it.
- **Concurrency:** Be explicit about threading/async choices. GPIO and serial libraries often have their own threading quirks — don't assume thread-safety.
- **Testing:** Unit-test anything that can be verified off-hardware. Clearly flag anything that requires the physical Pi to actually verify.

## Communication Preferences

- Flag trade-offs made for reliability (retries, extra validation, defensive checks) explicitly rather than burying them in a diff.
- If a suggested library or pattern has known quirks on Raspberry Pi (e.g. GPIO library behavior differing across Pi models/OS versions), say so up front.
- Default to concise, direct answers — no filler, no re-explaining fundamentals.

## Project-Specific Notes

- **What this is:** Python/Flask port of the ESP32-P4 "PL Connect" firmware
  (sibling `main/` in the monorepo) — a factory I/O web server / Modbus
  gateway. Byte-for-byte REST/WebSocket/MQTT API compatible with the
  firmware so the extracted `web/` dashboard runs unmodified against either
  backend. Full architecture reference: `doc/PI_PORT_REFERENCE.md` (the
  living maintainer doc for this repo specifically — keep it in sync, same
  spirit as rule 1 above).
- **Target hardware:** Raspberry Pi 4 **and** Pi 5, both — see
  `doc/PI_PORT_REFERENCE.md` §12 for the one place this ever mattered
  (`rpi-lgpio`, not real `RPi.GPIO`; real RPi.GPIO cannot address the Pi 5's
  RP1 I/O chip at all). 40-pin header BCM GPIO numbering is identical
  across both, so pin maps and driver logic are unmodified between them.
- **OS/distro:** Raspberry Pi OS **Bookworm or newer**, required for
  NetworkManager (`network_manager.py` shells out to `nmcli`; does not
  support the older `dhcpcd` stack) and because Pi 5 has no 32-bit image.
- **Key hardware peripherals:** 2 relay outputs (DO), 10 digital inputs
  (DI, with Normal/Counter modes), RS485 half-duplex over the Pi's hardware
  UART0 (`/dev/serial0`) with a bit-banged GPIO DE/nRE pair (no hardware
  RS485 mode on this SoC, unlike the ESP32), I2C SSD1306 OLED (device IP
  display), Ethernet + optional Wi-Fi.
- **Deployment/update mechanism:** systemd unit `pl-connect.service`
  (`Type=notify` + `WatchdogSec=30`, runs as root — see the unit file's own
  comments for the unprivileged alternative), `Restart=always`. In-field
  updates via the dashboard's `/update` page: password-gated `git fetch` +
  hard `checkout -B <branch> origin/<branch>` + service restart — **not**
  an OTA binary flash like the firmware, since this runs from a git
  checkout. `git pull`/`/update` do **not** reinstall Python dependencies —
  after any `requirements.txt` change, `venv/bin/pip install -r
  requirements.txt` is a required manual step post-pull, every time.
- **CI setup:** none currently. Verification is `python3 -m py_compile` /
  `node --check` locally plus manual bench-testing on real hardware for
  anything GPIO/serial/I2C-touching (RPi.GPIO/serial/I2C libraries fall back
  to no-op stubs off-Pi — see README's "Testing locally" section — so unit
  tests alone cannot verify hardware-facing behavior; flag explicitly
  whenever something needs the physical Pi to confirm, per the Testing rule
  above).