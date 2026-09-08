"""Publish-only MQTT status publisher. Port of modules/mqtt_manager.c."""
import json
import logging
import threading
import time

log = logging.getLogger("mqtt")

try:
    import paho.mqtt.client as mqtt

    _HAVE_PAHO = True
except ImportError:
    log.warning("paho-mqtt not installed - MQTT publishing disabled")
    _HAVE_PAHO = False

_client = None
_connected = False
_lock = threading.Lock()


def is_connected():
    with _lock:
        return _connected


def _on_connect(client, userdata, flags, rc, properties=None):
    global _connected
    with _lock:
        _connected = rc == 0
    if rc == 0:
        log.info("MQTT connected")
    else:
        log.warning("MQTT connect failed rc=%s", rc)


def _on_disconnect(client, userdata, rc, properties=None):
    global _connected
    with _lock:
        _connected = False


def _publish_loop(cfg):
    from app import state

    interval_s = max(cfg.get("mqtt_interval", 1000), 250) / 1000.0
    topic = cfg.get("mqtt_topic", "esp32/status")
    while True:
        if is_connected():
            try:
                payload = json.dumps(state.build_status_json())
                _client.publish(topic, payload, qos=0, retain=False)
            except Exception as e:
                log.warning("publish failed: %s", e)
        time.sleep(interval_s)


def init(cfg):
    """No-op if disabled - mirrors mqtt_manager_init()'s skip-when-disabled,
    never-abort-boot behavior."""
    global _client
    if not cfg.get("mqtt_enabled"):
        log.info("MQTT disabled - skipping init")
        return
    if not _HAVE_PAHO:
        return
    broker = cfg.get("mqtt_broker", "")
    port = cfg.get("mqtt_port", 1883)
    if not broker:
        log.warning("MQTT enabled but no broker configured - skipping")
        return

    _client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    _client.on_connect = _on_connect
    _client.on_disconnect = _on_disconnect
    try:
        _client.connect_async(broker, port, keepalive=30)
        _client.loop_start()
    except Exception as e:
        log.error("MQTT connect_async failed: %s", e)
        return

    threading.Thread(target=_publish_loop, args=(cfg,), name="mqtt-publish", daemon=True).start()
