"""Flask app: REST + /ws + static/page routes. Port of http_server.c.

Endpoint paths, methods and JSON field names mirror the firmware exactly
(extracted from http_server.c/app_js.cpp this session) so the extracted
web/ assets work completely unmodified against this backend.

One deliberate, documented deviation: the firmware always does a full
device reboot 500ms after a wifi/lan/mqtt/modbus/rs485 save (or /api/reboot
itself). Here that's rescoped to restarting this systemd service (re-runs
the equivalent of app_main() - reloads config, reinits every module)
instead of rebooting the whole Raspberry Pi OS. See the plan file.
"""
import json
import logging
import os
import threading
import time

from flask import Flask, jsonify, request, send_file
from flask_sock import Sock

from app import (
    config,
    gpio_driver,
    mqtt_manager,
    modbus_tcp_slave,
    modbus_rtu_master,
    network_manager,
    state,
    updater,
)

log = logging.getLogger("http")

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES_DIR = os.path.join(ROOT_DIR, "web", "pages")
STATIC_DIR = os.path.join(ROOT_DIR, "web", "static")

_VALID_PAGES = {"io", "config", "network", "system", "rs485"}

app = Flask(__name__, static_folder=None)
sock = Sock(app)


def _restart_task():
    time.sleep(0.5)
    log.info("restarting process (systemd Restart= policy re-launches it)")
    os._exit(0)


def schedule_restart():
    threading.Thread(target=_restart_task, name="restart", daemon=True).start()


# ---------------------------------------------------------------------------
# Static assets / page fragments
# ---------------------------------------------------------------------------

@app.route("/")
def root():
    resp = send_file(os.path.join(PAGES_DIR, "index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/style.css")
def style_css():
    return send_file(os.path.join(STATIC_DIR, "style.css"), mimetype="text/css")


@app.route("/app.js")
def app_js():
    return send_file(os.path.join(STATIC_DIR, "app.js"), mimetype="application/javascript")


@app.route("/page/<name>")
def page_fragment(name):
    if name not in _VALID_PAGES:
        return "not found", 404
    resp = send_file(os.path.join(PAGES_DIR, f"page_{name}.html"), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# ---------------------------------------------------------------------------
# Status / relay / channel config
# ---------------------------------------------------------------------------

@app.route("/api/status")
def api_status():
    return jsonify(state.build_status_json())


@app.route("/api/relay")
def api_relay():
    try:
        relay_id = int(request.args.get("id", ""))
        relay_state = int(request.args.get("state", ""))
    except (TypeError, ValueError):
        return "bad request", 400
    if relay_id < 0 or relay_id >= gpio_driver.NUM_RELAYS or relay_state not in (0, 1):
        return "bad request", 400
    if not gpio_driver.set_relay(relay_id, relay_state == 1):
        return "failed", 500
    return "OK"


@app.route("/api/channel-config")
def api_channel_config_get():
    return jsonify(config.load_channel_config())


@app.route("/api/save-channel-config", methods=["POST"])
def api_channel_config_save():
    body = request.get_json(force=True, silent=True) or {}
    saved = config.save_channel_config(body)
    # Applies live, same as the firmware - no restart needed for DI mode/invert.
    for i, ch in enumerate(saved["di"]):
        gpio_driver.set_di_mode(i, ch.get("mode", 0))
        gpio_driver.set_di_invert(i, ch.get("invert", False))
    return jsonify({"status": "saved"})


@app.route("/api/reset-counter")
def api_reset_counter():
    try:
        ch = int(request.args.get("ch", "-1"))
    except (TypeError, ValueError):
        ch = -1
    gpio_driver.reset_count(ch)
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Network (LAN + Wi-Fi)
# ---------------------------------------------------------------------------

@app.route("/api/network-config")
def api_network_config():
    cfg = config.load_config()
    return jsonify(
        {
            "wifi_ssid": cfg["wifi_ssid"],
            "wifi_ip": cfg["wifi_ip"],
            "wifi_subnet": cfg["wifi_subnet"],
            "wifi_gateway": cfg["wifi_gateway"],
            "wifi_dns": cfg["wifi_dns"],
            "wifi_static": cfg["wifi_static"],
            "wifi_enabled": cfg["wifi_enabled"],
            "lan_ip": cfg["lan_ip"],
            "lan_subnet": cfg["lan_subnet"],
            "lan_gateway": cfg["lan_gateway"],
            "lan_dns": cfg["lan_dns"],
            "lan_static": cfg["lan_static"],
        }
    )


@app.route("/api/save-wifi-config", methods=["POST"])
def api_save_wifi_config():
    body = request.get_json(force=True, silent=True) or {}
    cfg = config.save_wifi_config(body)
    network_manager.apply_wifi_config(cfg)
    schedule_restart()
    return jsonify({"status": "saved"})


@app.route("/api/save-lan-config", methods=["POST"])
def api_save_lan_config():
    body = request.get_json(force=True, silent=True) or {}
    cfg = config.save_lan_config(body)
    network_manager.apply_lan_config(cfg)
    schedule_restart()
    return jsonify({"status": "saved"})


# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------

@app.route("/api/mqtt-config")
def api_mqtt_config():
    cfg = config.load_config()
    return jsonify(
        {
            "enabled": cfg["mqtt_enabled"],
            "broker": cfg["mqtt_broker"],
            "port": cfg["mqtt_port"],
            "topic": cfg["mqtt_topic"],
            "interval": cfg["mqtt_interval"],
            "connected": mqtt_manager.is_connected(),
        }
    )


@app.route("/api/save-mqtt-config", methods=["POST"])
def api_save_mqtt_config():
    body = request.get_json(force=True, silent=True) or {}
    config.save_mqtt_config(body)
    schedule_restart()
    return jsonify({"status": "saved"})


# ---------------------------------------------------------------------------
# Modbus TCP
# ---------------------------------------------------------------------------

@app.route("/api/modbus-config")
def api_modbus_config():
    cfg = config.load_config()
    return jsonify(
        {
            "enabled": cfg["mb_enabled"],
            "port": cfg["mb_port"],
            "unit_id": cfg["mb_unit_id"],
            "running": modbus_tcp_slave.is_running(),
        }
    )


@app.route("/api/save-modbus-config", methods=["POST"])
def api_save_modbus_config():
    body = request.get_json(force=True, silent=True) or {}
    config.save_modbus_config(body)
    schedule_restart()
    return jsonify({"status": "saved"})


# ---------------------------------------------------------------------------
# RS485 / Modbus RTU
# ---------------------------------------------------------------------------

@app.route("/api/rs485-config")
def api_rs485_config():
    cfg = config.load_rs485_config()
    cfg = dict(cfg)
    cfg["running"] = modbus_rtu_master.is_running()
    return jsonify(cfg)


@app.route("/api/save-rs485-config", methods=["POST"])
def api_save_rs485_config():
    body = request.get_json(force=True, silent=True) or {}
    config.save_rs485_config(body)
    schedule_restart()
    return jsonify({"status": "saved"})


# ---------------------------------------------------------------------------
# Reboot / update
# ---------------------------------------------------------------------------

@app.route("/api/reboot", methods=["POST"])
def api_reboot():
    schedule_restart()
    return jsonify({"status": "restarting"})


@app.route("/update")
def update_page():
    return send_file(os.path.join(PAGES_DIR, "update.html"))


def _check_update_password():
    expected = config.load_update_config().get("update_password", "123456")
    supplied = request.headers.get("X-OTA-Password", "")
    return supplied == expected


@app.route("/api/update-config")
def api_update_config():
    cfg = config.redact_update_config()
    cfg["ssh_key_set"] = updater.has_ssh_key()
    return jsonify(cfg)


@app.route("/api/save-update-config", methods=["POST"])
def api_save_update_config():
    # Gated behind the same password as the update action itself, unlike
    # every other config endpoint - this one stores credentials that get
    # used to pull-and-run arbitrary code as whatever user this service
    # runs as (root, by default), so it doesn't get the usual "anyone on
    # the LAN dashboard can change it" treatment.
    if not _check_update_password():
        return jsonify({"status": "error", "message": "Incorrect password"}), 401

    body = request.get_json(force=True, silent=True) or {}
    config.save_update_config(body)
    ssh_key = body.get("ssh_key")
    if isinstance(ssh_key, str) and ssh_key.strip():
        updater.save_ssh_key(ssh_key)
    return jsonify({"status": "saved"})


@app.route("/update", methods=["POST"])
def update_post():
    if not _check_update_password():
        return "Incorrect password", 401

    ok, message = updater.run_git_update(ROOT_DIR)
    if not ok:
        return message, 500

    schedule_restart()
    return f"{message} — restarting..."


# ---------------------------------------------------------------------------
# WebSocket - full status snapshot on connect, then push on any change
# ---------------------------------------------------------------------------

@sock.route("/ws")
def ws_route(ws):
    state.register_ws_client(ws)
    try:
        ws.send(json.dumps(state.build_status_json()))
    except Exception:
        state.unregister_ws_client(ws)
        return
    try:
        while True:
            msg = ws.receive()  # pure server->client push protocol - discard anything received
            if msg is None:
                break
    finally:
        state.unregister_ws_client(ws)


def start():
    """Run the Flask/WS server - blocking, call from a dedicated thread or
    as the main process (mirrors http_server_start() being the last thing
    main.c's boot sequence kicks off). Port 80 needs root; override with
    the PORT env var for unprivileged local testing (e.g. PORT=8080)."""
    port = int(os.environ.get("PORT", "80"))
    app.run(host="0.0.0.0", port=port, threaded=True)
