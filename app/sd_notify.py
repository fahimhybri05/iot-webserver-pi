"""systemd readiness/watchdog notification - sd_notify protocol implemented
directly over a Unix datagram socket (no python-systemd/sdnotify pip
dependency needed for something this small). No firmware equivalent - the
ESP32 has no supervisor process to notify.

Both ready() and start_watchdog() are no-ops unless the relevant systemd
env vars are actually present, so this is silently inert when run outside
systemd (e.g. local `PORT=8080 python -m app.main` testing per README) and
never raises into the caller - this is pure supervision plumbing, not a
functional dependency, so a failure here must never affect the app itself.
"""
import logging
import os
import socket
import threading
import time

log = logging.getLogger("sd_notify")

# Ping at half the configured WatchdogSec - systemd's own recommended
# margin, so a single missed/delayed tick doesn't trip a false restart.
_WATCHDOG_PING_DIVISOR = 2


def _send(message):
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]  # abstract namespace socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(addr)
            sock.sendall(message.encode())
    except Exception as e:
        log.warning("sd_notify(%s) failed: %s", message, e)


def ready():
    """Call once, after boot/init is fully complete - tells systemd
    (unit needs Type=notify) the service is genuinely up, not just forked.
    Only reachable in main.py's boot sequence once every peripheral init
    has returned (they're all individually failure-tolerant and never
    raise), so reaching this call is itself a meaningful "actually booted
    cleanly" signal, not just "the process exists"."""
    _send("READY=1")


def _watchdog_loop(interval_s):
    while True:
        time.sleep(interval_s)
        _send("WATCHDOG=1")


def start_watchdog():
    """No-op unless systemd set both $NOTIFY_SOCKET and $WATCHDOG_USEC (the
    latter only present when the unit sets WatchdogSec=). Starts a
    background thread pinging at half that interval so a genuine hang
    (process alive but stuck - e.g. a future blocking call that never
    returns) gets systemd to kill and restart it, not just a crash/exit."""
    if not os.environ.get("NOTIFY_SOCKET"):
        return
    watchdog_usec = os.environ.get("WATCHDOG_USEC")
    if not watchdog_usec:
        return
    try:
        interval_s = (int(watchdog_usec) / 1_000_000) / _WATCHDOG_PING_DIVISOR
    except ValueError:
        return
    if interval_s <= 0:
        return
    threading.Thread(target=_watchdog_loop, args=(interval_s,), name="sd-watchdog", daemon=True).start()
    log.info("systemd watchdog pings started, interval=%.1fs", interval_s)
