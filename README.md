# PL Connect — Raspberry Pi port

Python port of the ESP32-P4 "PL Connect" firmware (`../main/`), for the
Raspberry Pi 4 variant of the same carrier board. Same REST/WebSocket API,
same dashboard, same register maps — see `../memory/rpi4_esp32p4_gpio_reference.md`
and the port's plan file for how the GPIO mapping was re-derived from the
board schematic (do **not** reuse the ESP32 GPIO numbers from the C source —
they're meaningless on a Pi).

**New Pi, starting from scratch? See [`SETUP.md`](SETUP.md)** for the full
step-by-step (flashing, config.txt, install, systemd, wiring table,
troubleshooting). This README is the quick reference.

## Pi OS setup (one-time, before first run)

Edit `/boot/firmware/config.txt` (Raspberry Pi OS Bookworm+; `/boot/config.txt`
on older releases):

```
dtparam=i2c_arm=on
enable_uart=1
dtoverlay=disable-bt
```

`dtoverlay=disable-bt` frees GPIO14/15 from Bluetooth so the RS485 UART lands
on `/dev/ttyAMA0` (symlinked as `/dev/serial0`) instead of the mini-UART —
same tradeoff the firmware's README documents for its own RS485 pin choice
(no serial console on these pins while RS485 uses them). Then:

```
sudo systemctl disable serial-getty@ttyAMA0.service   # free the UART from the login console
sudo reboot
```

## Install

```
python3 -m venv /opt/pl-connect/venv
/opt/pl-connect/venv/bin/pip install -r requirements.txt
sudo cp pl-connect.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pl-connect
```

Dashboard: `http://<pi-ip>/`

## Testing locally (not on the Pi appliance)

Safe to run directly on a dev machine (`PORT=8080 venv/bin/python -m app.main`)
to poke at the dashboard/API/WebSocket — GPIO/serial/I2C libraries all fall
back to no-op stubs when there's no real hardware.

**Except network config.** `network_manager.apply_lan_config()` /
`apply_wifi_config()` are gated behind the `PL_CONNECT_APPLY_NETWORK=1`
env var (unset by default) specifically so this can't happen: without the
gate, main.py's boot sequence unconditionally applies the stored LAN config
via `nmcli` at every startup, which on a real Pi appliance is the point —
but run the same code on your own laptop/desktop and it will just as
happily repoint *that* machine's real Ethernet connection to a static
192.168.1.101/24, which is exactly what it did during this port's own
first test run. Leave `PL_CONNECT_APPLY_NETWORK` unset for any local
testing; the systemd unit sets it to `1` for the real deployment.

## Layout

- `app/` — the Python modules, one per original C module (see the port plan
  file for the full C-source -> Python file map).
- `web/` — the dashboard, extracted near-verbatim from the firmware's
  embedded HTML/CSS/JS (`main/ui/*.cpp` in the ESP32 firmware).
- `data/` — runtime config (JSON files, replacing the firmware's NVS
  namespaces). Created automatically on first run; back this up if you
  care about saved channel labels/RS485 slave configs.

## Notes / deviations from the firmware

- **Network config really applies** (via `nmcli`) — a bad static IP/gateway
  here can drop the Pi off the network the same way a bad `esp_netif` config
  would on the ESP32. Test over a monitor/keyboard or a second network path
  the first time, not the SSH session you'd lose.
- **"Reboot to apply"** is rescoped to *restart the `pl-connect` service*
  (re-reads config, reinits every module) rather than rebooting the whole Pi
  OS — see the plan file for why.
- **`/update`** does a `git fetch` + `checkout -B <branch> origin/<branch>` +
  service restart, password-gated the same "casual-push deterrent, not real
  auth" way the firmware's OTA page was. Repo URL, branch, and auth
  (none / HTTPS token / SSH key) are configured from the Update page itself
  (`/api/update-config`, `/api/save-update-config` — the save endpoint is
  the one config endpoint in this whole app that requires the update
  password, since it stores credentials used to pull-and-run code as
  whatever user the service runs as). Token and SSH key are never sent back
  out through the API once saved — the update page only shows whether one
  is set. The SSH private key is written to `data/deploy_key` (0600); the
  token lives in `data/update.json` alongside the update password. **Change
  the default `123456` password before exposing this off your LAN** — same
  requirement the original firmware's hardcoded OTA password had.
