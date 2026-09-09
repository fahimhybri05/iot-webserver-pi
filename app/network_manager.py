"""LAN (Ethernet) + Wi-Fi config, applied for real via nmcli.

Port of modules/network.c + modules/wifi_manager.c. Unlike those two (ESP-IDF
esp_netif calls), this shells out to NetworkManager - the standard network
stack on Raspberry Pi OS. Every command run is logged at INFO so a bad save
is traceable from the service log, since a wrong static IP/gateway here can
drop the Pi off the network the same way a wrong esp_netif config would.

Requires the service to have permission to talk to NetworkManager (run as
root, or a polkit rule granting the service user org.freedesktop.NetworkManager.*).

SAFETY GATE: apply_lan_config()/apply_wifi_config() are no-ops unless the
PL_CONNECT_APPLY_NETWORK=1 environment variable is set. This code assumes it
owns the box's only network interfaces (true on the dedicated Pi appliance
this was written for) - running it unguarded on a developer's own machine
(or anything else with a network config worth keeping) would silently
reconfigure their real NIC to this app's stored LAN settings on every
startup. main.py's boot sequence calls apply_lan_config() unconditionally
(mirroring the firmware always applying LAN config at boot), so this gate is
the only thing standing between "python -m app.main" on a random Ubuntu box
and a live static-IP change to that box's Ethernet connection - learned the
hard way once already, do not remove without a better safeguard in its place.
The systemd unit sets this variable for real deployments.
"""
import logging
import os
import re
import subprocess
import threading
import time

log = logging.getLogger("network")

_APPLY_ENABLED = os.environ.get("PL_CONNECT_APPLY_NETWORK", "0") == "1"

# Adjust if your Pi's interface names differ from the Raspberry Pi OS default
# (check with `nmcli device status`). Auto-detected at first use as a fallback.
ETH_IFACE_DEFAULT = "eth0"
WIFI_IFACE_DEFAULT = "wlan0"

_IPV4_RE = re.compile(
    r"^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$"
)

_lock = threading.Lock()
_iface_cache = {}


def _valid_ipv4(s):
    return bool(s) and bool(_IPV4_RE.match(s))


def _run(args, level=logging.INFO):
    # level defaults to INFO so a config-apply command (rare, and a bad
    # static IP/gateway can drop the Pi off the network - see module
    # docstring) stays traceable in the service log. The routine 2s
    # status-poll queries (_query_lan_ip/_query_wifi_ip) pass DEBUG instead
    # - logging every single one at INFO forever would drown out everything
    # else in `journalctl -f` for no benefit, since nothing ever needs to
    # trace a read-only IP lookup after the fact.
    log.log(level, "nmcli %s", " ".join(args))
    try:
        r = subprocess.run(
            ["nmcli"] + args, capture_output=True, text=True, timeout=15
        )
        if r.returncode != 0:
            log.error("nmcli %s failed: %s", " ".join(args), r.stderr.strip())
        return r
    except (OSError, subprocess.SubprocessError) as e:
        log.error("nmcli not runnable: %s", e)
        return None


def _find_device(dev_type, fallback):
    key = dev_type
    if key in _iface_cache:
        return _iface_cache[key]
    r = _run(["-t", "-f", "DEVICE,TYPE", "device", "status"])
    iface = fallback
    if r and r.returncode == 0:
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) == 2 and parts[1] == dev_type:
                iface = parts[0]
                break
    _iface_cache[key] = iface
    return iface


def _eth_iface():
    return _find_device("ethernet", ETH_IFACE_DEFAULT)


def _wifi_iface():
    return _find_device("wifi", WIFI_IFACE_DEFAULT)


def _active_connection_for(iface):
    r = _run(["-t", "-f", "GENERAL.CONNECTION", "device", "show", iface])
    if r and r.returncode == 0 and r.stdout.strip():
        name = r.stdout.strip().split(":", 1)[-1]
        if name and name != "--":
            return name
    return None


def _netmask_to_prefix(mask):
    if not _valid_ipv4(mask):
        return 24
    try:
        return sum(bin(int(o)).count("1") for o in mask.split("."))
    except ValueError:
        return 24


def _query_lan_ip():
    iface = _eth_iface()
    r = _run(["-g", "IP4.ADDRESS", "device", "show", iface], level=logging.DEBUG)
    if r and r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().split("|")[0].split("/")[0]
    return "0.0.0.0"


def _query_wifi_ip():
    iface = _wifi_iface()
    r = _run(["-g", "IP4.ADDRESS", "device", "show", iface], level=logging.DEBUG)
    if r and r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().split("|")[0].split("/")[0]
    return ""


# get_lan_ip()/get_wifi_ip() feed state.build_status_json(), which runs on
# every REST /api/status call AND every WebSocket broadcast (any DI/relay
# change) - potentially many times a second. Calling nmcli synchronously on
# that hot path means spawning 1-2 subprocesses per status build, which both
# wastes CPU on a Pi and adds scheduling jitter that competes with anything
# else that cares about tight timing (e.g. the RS485 poll thread's
# millisecond-level DE settle delays). An IP address changes on the order of
# minutes, not milliseconds, so a background poll + cached read is strictly
# better than querying live on every call.
_NET_POLL_PERIOD_S = 2.0
_net_cache_lock = threading.Lock()
_cached_lan_ip = "0.0.0.0"
_cached_wifi_ip = ""
_net_poll_started = False


def _net_poll_loop():
    global _cached_lan_ip, _cached_wifi_ip
    while True:
        try:
            lan_ip = _query_lan_ip()
            wifi_ip = _query_wifi_ip()
            with _net_cache_lock:
                _cached_lan_ip = lan_ip
                _cached_wifi_ip = wifi_ip
        except Exception:
            log.exception("network status poll failed - keeping last known values")
        time.sleep(_NET_POLL_PERIOD_S)


def _ensure_net_poll_started():
    global _net_poll_started
    with _lock:
        if _net_poll_started:
            return
        _net_poll_started = True
        threading.Thread(target=_net_poll_loop, name="net-status-poll", daemon=True).start()


def get_lan_ip():
    _ensure_net_poll_started()
    with _net_cache_lock:
        return _cached_lan_ip


def get_wifi_ip():
    _ensure_net_poll_started()
    with _net_cache_lock:
        return _cached_wifi_ip


def is_wifi_connected():
    return get_wifi_ip() != ""


def apply_lan_config(cfg):
    """cfg: the loaded system Config dict (lan_ip/lan_subnet/lan_gateway/lan_dns/lan_static).
    No-op unless PL_CONNECT_APPLY_NETWORK=1 - see module docstring."""
    if not _APPLY_ENABLED:
        log.info("PL_CONNECT_APPLY_NETWORK not set - not touching real network config (LAN)")
        return
    with _lock:
        iface = _eth_iface()
        conn = _active_connection_for(iface) or iface
        if not cfg.get("lan_static"):
            _run(["con", "mod", conn, "ipv4.method", "auto"])
            _run(["con", "up", conn])
            return
        ip = cfg.get("lan_ip", "")
        gw = cfg.get("lan_gateway", "")
        if not _valid_ipv4(ip) or not _valid_ipv4(gw):
            log.error("refusing to apply LAN static config - invalid ip=%r gateway=%r", ip, gw)
            return
        prefix = _netmask_to_prefix(cfg.get("lan_subnet", "255.255.255.0"))
        dns = cfg.get("lan_dns", "")
        args = [
            "con", "mod", conn,
            "ipv4.method", "manual",
            "ipv4.addresses", f"{ip}/{prefix}",
            "ipv4.gateway", gw,
        ]
        if dns and _valid_ipv4(dns):
            args += ["ipv4.dns", dns]
        _run(args)
        _run(["con", "up", conn])


def apply_wifi_config(cfg):
    """cfg: the loaded system Config dict. No-op if wifi_enabled is false or
    PL_CONNECT_APPLY_NETWORK isn't set - see module docstring."""
    if not _APPLY_ENABLED:
        log.info("PL_CONNECT_APPLY_NETWORK not set - not touching real network config (Wi-Fi)")
        return
    with _lock:
        if not cfg.get("wifi_enabled"):
            return
        ssid = cfg.get("wifi_ssid", "")
        if not ssid or ssid == "Your_SSID":
            log.warning("wifi_enabled but no real SSID configured - skipping")
            return
        password = cfg.get("wifi_pass", "")
        iface = _wifi_iface()
        args = ["device", "wifi", "connect", ssid, "ifname", iface]
        if password:
            args += ["password", password]
        _run(args)

        if not cfg.get("wifi_static"):
            return
        conn = _active_connection_for(iface) or ssid
        ip = cfg.get("wifi_ip", "")
        gw = cfg.get("wifi_gateway", "")
        if not _valid_ipv4(ip) or not _valid_ipv4(gw):
            log.error("refusing to apply Wi-Fi static config - invalid ip=%r gateway=%r", ip, gw)
            return
        prefix = _netmask_to_prefix(cfg.get("wifi_subnet", "255.255.255.0"))
        dns = cfg.get("wifi_dns", "")
        mod_args = [
            "con", "mod", conn,
            "ipv4.method", "manual",
            "ipv4.addresses", f"{ip}/{prefix}",
            "ipv4.gateway", gw,
        ]
        if dns and _valid_ipv4(dns):
            mod_args += ["ipv4.dns", dns]
        _run(mod_args)
        _run(["con", "up", conn])


def init(cfg):
    """Apply persisted network config at startup - mirrors network_init() +
    wifi_manager_init() both running unconditionally in main.c's boot sequence
    (LAN always applies; Wi-Fi only connects if enabled)."""
    apply_lan_config(cfg)
    apply_wifi_config(cfg)
