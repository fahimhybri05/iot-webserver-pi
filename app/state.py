"""Shared live status + WebSocket broadcast.

Equivalent of http_server_build_status_json() + the WS push half of
http_server.c. Every module (gpio_driver, modbus_rtu_master, ...) calls broadcast()
whenever something visible in the status JSON changes; this mirrors
gpio_set_change_callback(http_server_push_state) in the firmware,
generalized to any source instead of just GPIO.

Module getters are imported lazily inside build_status_json() to avoid a
circular top-level import (gpio_driver etc. import this module too, to call
notify_change()).
"""
import json
import threading

FIRMWARE_VERSION = "1.1.4"
FIRMWARE_APP_NAME = "PL Connect"
FIRMWARE_COPYRIGHT = "2026 - PL Connect. All rights reserved. (Raspberry Pi port)"

_ws_clients = set()
_ws_lock = threading.Lock()


def _detect_board():
    """Reads /proc/device-tree/model once at import time - board doesn't
    change at runtime. Pi 4 and Pi 5 use different SoCs (BCM2711/BCM2712);
    the dashboard used to hardcode "Broadcom BCM2711", which is simply wrong
    on a Pi 5. Falls back to "Unknown" off-Pi (dev machine) or on any read
    error, same never-crash spirit as every other optional peripheral."""
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read().strip("\x00\n \t")
    except OSError:
        model = "Unknown"
    if "Raspberry Pi 5" in model:
        cpu = "Broadcom BCM2712"
    elif "Raspberry Pi 4" in model:
        cpu = "Broadcom BCM2711"
    else:
        cpu = "Unknown"
    return model or "Unknown", cpu


BOARD_MODEL, BOARD_CPU = _detect_board()


def register_ws_client(ws):
    with _ws_lock:
        _ws_clients.add(ws)


def unregister_ws_client(ws):
    with _ws_lock:
        _ws_clients.discard(ws)


def build_status_json():
    """Returns a dict with the exact same key set/order as the firmware's
    http_server_build_status_json(): ip, wifi_ip, wifi_connected, version,
    copyright, relays[], inputs[], counts[], modbus_rtu[]."""
    from app import gpio_driver, network_manager, modbus_rtu_master

    return {
        "ip": network_manager.get_lan_ip(),
        "wifi_ip": network_manager.get_wifi_ip(),
        "wifi_connected": network_manager.is_wifi_connected(),
        "version": FIRMWARE_VERSION,
        "copyright": FIRMWARE_COPYRIGHT,
        "board_model": BOARD_MODEL,
        "board_cpu": BOARD_CPU,
        "relays": gpio_driver.get_relay_states(),
        "inputs": gpio_driver.get_input_states(),
        "counts": gpio_driver.get_counts(),
        "modbus_rtu": modbus_rtu_master.get_status_json(),
    }


def broadcast():
    """Push the current status to every connected WS client - best effort,
    silently drops clients that error out (mirrors the firmware only logging
    a WARN on a dead fd, never letting one bad client break the round)."""
    payload = json.dumps(build_status_json())
    with _ws_lock:
        clients = list(_ws_clients)
    for ws in clients:
        try:
            ws.send(payload)
        except Exception:
            unregister_ws_client(ws)
