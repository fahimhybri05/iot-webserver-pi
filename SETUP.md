# Setting up PL Connect on a Raspberry Pi 4 or 5

Step-by-step from a bare Pi to a running dashboard. This is the "PL-CONNECT"
carrier board (same board the ESP32-P4 firmware in `../main/` targets) with
a Raspberry Pi 4 or 5 plugged into its 40-pin header instead of the ESP32-P4
dev kit. Both boards use the exact same 40-pin BCM GPIO numbering and the
exact same steps below — the only Pi-generation-specific detail anywhere in
this codebase is the GPIO access library (`rpi-lgpio` in `requirements.txt`,
handled automatically in step 5).

## 0. What you need

- Raspberry Pi 4 **or** Pi 5, plugged into the PL-CONNECT board's 40-pin
  header.
- Raspberry Pi OS **Bookworm or newer** (uses NetworkManager by default —
  older releases used `dhcpcd`, which `app/network_manager.py` does not
  support). Pi 5 requires the 64-bit image regardless — there is no 32-bit
  Raspberry Pi OS build for it.
- Network access to the Pi (Ethernet recommended for first setup, since
  Wi-Fi is configured *from* the dashboard, which needs network to reach in
  the first place — chicken-and-egg if you start Wi-Fi-only).
- An SSH session or a monitor/keyboard on the Pi.

## 1. Flash and boot Raspberry Pi OS

Use Raspberry Pi Imager (or `rpi-imager` CLI). In the imager's advanced
options (gear icon / Ctrl+Shift+X), it's worth setting your SSH key and
hostname up front so you don't need a monitor at all.

## 2. Enable I2C + free the UART for RS485

The OLED (I2C) and RS485 (hardware UART0) both need one-time firmware
config changes. Edit `/boot/firmware/config.txt` (Bookworm; it's
`/boot/config.txt` on older Raspberry Pi OS releases):

```
sudo nano /boot/firmware/config.txt
```

Add (or uncomment) these lines:

```
dtparam=i2c_arm=on
enable_uart=1
dtoverlay=disable-bt
```

- `dtparam=i2c_arm=on` — enables I2C1 on GPIO2/GPIO3 (header pins 3/5), used
  by the SSD1306 OLED.
- `dtoverlay=disable-bt` — the Pi's built-in Bluetooth normally claims the
  good PL011 UART, leaving GPIO14/15 (header pins 8/10) on the limited
  mini-UART. Disabling Bluetooth frees the PL011 onto GPIO14/15 instead, so
  RS485 gets the real hardware UART as `/dev/ttyAMA0` (symlinked
  `/dev/serial0`). This is the same "no serial console while RS485 uses
  these pins" tradeoff the ESP32-P4 firmware's own README documents for its
  version of this board. Same overlay name and same tradeoff on Pi 5's RP1
  chip as on Pi 4's BCM2711 — not independently re-confirmed on physical
  Pi 5 hardware during this pass, so if `/dev/serial0` doesn't show up after
  reboot on a Pi 5, that overlay behavior is the first thing to double-check
  with `i2cdetect`/`ls -l /dev/serial0` from step 2's verification below.

Then free the UART from the login console and reboot:

```
sudo systemctl disable serial-getty@ttyAMA0.service
sudo raspi-config nonint do_serial_hw 0   # ensure the UART peripheral itself is enabled
sudo reboot
```

After reboot, confirm:

```
ls -l /dev/serial0        # should symlink to /dev/ttyAMA0
i2cdetect -y 1             # should run without error (needs: sudo apt install -y i2c-tools)
```

If the OLED is wired up, `i2cdetect -y 1` should show a device at address
`0x3c`.

## 3. Install prerequisites

```
sudo apt update
sudo apt install -y git python3-venv python3-pip i2c-tools
```

NetworkManager (`nmcli`) and `git` are already present on stock Raspberry
Pi OS Bookworm — nothing else to install for those.

## 4. Get the code onto the Pi

This lives in the same monorepo as the ESP32-P4 firmware
(`main/`, `memory/`, etc alongside `iot-webserver-pi/`), so a plain clone
pulls all of that too. **Clone it, don't `cp` it** — the dashboard's
`/update` page later does `git fetch` + `checkout` inside this directory
(step 8), which needs a real `.git` here. A `cp -r` copy has no `.git` and
silently breaks that feature (`git fetch` fails with "not a git repository").

```
sudo mkdir -p /opt/pl-connect-src
sudo chown $USER /opt/pl-connect-src
git clone --branch main https://github.com/fahimhybri05/iot-webserver-pi.git /opt/pl-connect-src
```

Point everything else (systemd's `WorkingDirectory`, and where you `cd`
for the steps below) at `/opt/pl-connect-src/iot-webserver-pi` — that
subfolder is where `app/`, `web/`, `requirements.txt` etc actually live.
Git commands run from inside a subfolder still operate correctly on the
whole repo (confirmed this session), so `/update` pulling from a monorepo
works with no code changes — it just means the Pi's disk also holds the
ESP32 firmware sources it'll never run. If you'd rather not carry that
along, `git sparse-checkout` can limit the clone to just
`iot-webserver-pi/` — ask if you want that wired in; it's a few extra
one-time commands, not a code change either way.

If your fork's repo root instead **is** `iot-webserver-pi/` directly (no
surrounding `main/`/`memory/`), just clone straight to
`/opt/pl-connect-src` and skip the subfolder nesting.

## 5. Python environment

From here on, "the app directory" means wherever step 4 left `app/`,
`web/`, `requirements.txt` etc — `/opt/pl-connect-src/iot-webserver-pi` in
the monorepo layout, or `/opt/pl-connect-src` if you cloned a
Pi-port-only fork directly.

```
cd /opt/pl-connect-src/iot-webserver-pi
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

`rpi-lgpio` only installs on ARM (the `requirements.txt` entry is
platform-gated) — this step is where you'd notice if you're accidentally
doing this on a non-Pi machine, it'll just be silently skipped there.
`rpi-lgpio` is a drop-in replacement for `RPi.GPIO` (same `import RPi.GPIO`
in the code) that works on **both** Pi 4 and Pi 5 — real RPi.GPIO cannot
address the Pi 5's RP1 I/O chip and fails outright there. If a system-wide
`python3-rpi.gpio` (real RPi.GPIO) is also installed via `apt`, it can
shadow the venv's `rpi-lgpio` on some setups — if GPIO calls fail on a Pi 5
with an error naming the SoC/peripheral base address, `sudo apt remove
python3-rpi.gpio` and recreate the venv.

## 6. Test it before installing as a service

Run it directly first, on a non-privileged port, so you can see log output
and fix problems before wiring it into systemd:

```
PORT=8080 venv/bin/python -m app.main
```

Open `http://<pi-ip>:8080/` in a browser. You should see the dashboard;
I/O Monitor should show all 10 DI low and both relays off (or real states,
if something's already wired and driving them).

**Do not set `PL_CONNECT_APPLY_NETWORK=1` for this step.** Without it,
`network_manager.py` only *reads* network state (safe) and skips every
`nmcli` command that would *change* it — deliberately, since this app
assumes it owns the box's network interfaces, which is only true once it's
actually deployed as the appliance. Ctrl+C to stop once you've confirmed
the dashboard loads and the log looks sane.

Check the log for what got skipped vs. what's genuinely missing hardware
(all fine at this stage — MQTT/Modbus TCP/RS485 are disabled by default
until you configure them from the dashboard; the OLED warning is expected
if you haven't wired one up yet):

```
network INFO PL_CONNECT_APPLY_NETWORK not set - not touching real network config (LAN)
mqtt INFO MQTT disabled - skipping init
modbus_tcp INFO Modbus TCP disabled - skipping init
modbus_rtu INFO RS485 Modbus RTU master disabled - skipping init
oled WARNING SSD1306 not responding - skipping OLED (...)
```

## 7. Install as a systemd service

```
sudo cp pl-connect.service /etc/systemd/system/
sudo nano /etc/systemd/system/pl-connect.service   # set WorkingDirectory/ExecStart to your app directory (step 5)
sudo systemctl daemon-reload
sudo systemctl enable --now pl-connect
sudo systemctl status pl-connect
journalctl -u pl-connect -f       # live log
```

The shipped unit runs as `root` and sets `PL_CONNECT_APPLY_NETWORK=1` — see
`pl-connect.service`'s comments for what root buys you (port 80,
`/dev/gpiomem`, `/dev/serial0`, `/dev/i2c-1`, NetworkManager access) and
how to run unprivileged instead if you'd rather not.

Dashboard is now at `http://<pi-ip>/` (port 80).

## 8. Lock down the update mechanism

The Update page (`/update` in the System tab, or `http://<pi-ip>/update`
directly) does `git fetch` + `checkout -B <branch> origin/<branch>` inside
the app directory (step 4/5) and restarts the service — this only works
because that directory (or an ancestor of it, in the monorepo layout) is a
real git checkout with a working `origin` remote; git finds the repo root
on its own even when run from the nested `iot-webserver-pi/` subfolder.

1. Open `http://<pi-ip>/update`.
2. Fill in **Repo URL** / **Branch** / **Auth Method** if you're not using
   the same repo/branch it was cloned from. For a private repo, pick Token
   (paste an access token — GitHub's `x-access-token` convention; if your
   remote is Bitbucket/GitLab instead, check that their token needs the
   same Basic-auth shape before relying on this) or SSH Key (paste a
   deploy key's private key). Neither is ever sent back to the browser
   once saved — the page only shows whether one is stored.
3. **Change the Update Password from the default `123456`** before this
   Pi is reachable from anywhere you don't fully trust — it gates both
   saving these credentials and triggering a pull, and the service usually
   runs as root.

## 9. Wire up the I/O and configure from the dashboard

GPIO pin mapping (Pi BCM numbers, re-derived from the board schematic — see
`../memory/rpi4_esp32p4_gpio_reference.md` and the port's plan file for how):

| Signal | Pi BCM GPIO | Header pin |
|---|---|---|
| DI0..DI9 | 16,19,20,26,21,25,5,12,6,13 | 36,35,38,37,40,22,29,32,31,33 |
| DO0 (relay 0 / RLY1) | 22 | 15 |
| DO1 (relay 1 / RLY2) | 27 | 13 |
| RS485 TX/RX | 14/15 (hw UART0) | 8/10 |
| RS485 DE/nRE | 23/24 | 16/18 |
| OLED SDA/SCL | 2/3 (hw I2C1) | 3/5 |

Everything else is configured live from the dashboard, no file editing
needed: Channel Config (DI/DO labels, modes, invert), Network (LAN static
IP / Wi-Fi — applies via `nmcli` once the service has
`PL_CONNECT_APPLY_NETWORK=1`, i.e. once running as the real systemd
service, not the step-6 test run), MQTT, Modbus TCP, and RS485 slaves.

## Troubleshooting

| Symptom | Check |
|---|---|
| Service won't start / port 80 in use | `journalctl -u pl-connect -e`; confirm nothing else (e.g. Apache/nginx) is on port 80; confirm running as root or with `CAP_NET_BIND_SERVICE` |
| `/dev/serial0` missing or RS485 silent | Did you disable Bluetooth (`dtoverlay=disable-bt`) and reboot? `ls -l /dev/serial0`; also disable the serial console (step 2) — a getty holding the port will fight the app for it |
| OLED never comes up | `i2cdetect -y 1` should show `3c`; check wiring on pins 3/5; confirm `dtparam=i2c_arm=on` and reboot happened |
| Static IP save breaks connectivity | You're testing live network changes — see the safety note in step 6. Recheck with `nmcli con show <connection-name>`; worst case, `nmcli con mod <name> ipv4.method auto && nmcli con up <name>` gets you back to DHCP |
| `/update` fails with "fetch failed" | Check Repo URL/branch/auth on the Update page; for a private repo, confirm the token/key actually has read access; `journalctl -u pl-connect` shows the exact git error (credentials themselves are never logged) |
| Relay/DI does nothing | Confirm wiring against the pin table above (Pi **BCM** numbers, not board header pin numbers, not the ESP32-P4 firmware's GPIO numbers — all three are different for the same physical pin) |
