# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Developer Profile

- 7+ years of professional experience in Python and embedded firmware development for Raspberry Pi platforms.
- Comfortable with hardware interfacing (GPIO, I2C, SPI, UART/serial), real-time constraints, and long-running unattended deployments.
- Expects senior-level engineering judgment in code and suggestions — skip beginner explanations of Python or basic embedded concepts.

## Non-Negotiable Rules

1. **Always update the version file whenever code changes.**
   - Location: `VERSION` (adjust this line to match wherever this repo actually tracks version — e.g. `version.py`, `__init__.py`, `setup.cfg`)
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

_(Fill in as needed: target Pi model(s), OS/distro, primary firmware purpose, key hardware peripherals, deployment/update mechanism, CI setup.)_