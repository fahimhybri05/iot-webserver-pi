"""Persistent config, replacing the firmware's NVS namespaces with JSON files.

Three files under DATA_DIR, one per original NVS namespace:
  system.json   - Config struct (network + MQTT + Modbus TCP)      [was NVS "system"]
  channels.json - ChannelConfig struct + DI counters                [was NVS "channels"]
  rs485.json    - Rs485Config struct (bus + dynamic slave/register list) [was NVS "rs485"]

Each save_* function mirrors the C source's partial-update semantics: a field
missing or wrong-typed in the incoming dict leaves the stored value untouched.
"""
import json
import os
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

NUM_DI = 10
NUM_DO = 2
CH_LABEL_LEN = 24

RS485_MAX_SLAVES = 40
RS485_MAX_REGS_PER_SLAVE = 16
RS485_LABEL_LEN = 24
RS485_UNIT_LEN = 12

RS485_REG_HOLDING = 0
RS485_REG_INPUT = 1
RS485_REG_COIL = 2
RS485_REG_DISCRETE = 3

RS485_DT_U16 = 0
RS485_DT_S16 = 1
RS485_DT_U32 = 2
RS485_DT_S32 = 3
RS485_DT_F32 = 4
RS485_DT_BIT = 5

RS485_STOP_BITS_1 = 0
RS485_STOP_BITS_1_5 = 1
RS485_STOP_BITS_2 = 2

DEFAULT_SYSTEM = {
    "wifi_ssid": "Your_SSID",
    "wifi_pass": "",
    "wifi_ip": "192.168.1.101",
    "wifi_subnet": "255.255.255.0",
    "wifi_gateway": "192.168.1.1",
    "wifi_dns": "",
    "wifi_static": True,
    "wifi_enabled": False,
    "lan_ip": "192.168.1.101",
    "lan_subnet": "255.255.255.0",
    "lan_gateway": "192.168.1.1",
    "lan_dns": "",
    "lan_static": True,
    "mqtt_enabled": False,
    "mqtt_broker": "broker.hivemq.com",
    "mqtt_port": 1883,
    "mqtt_topic": "esp32/status",
    "mqtt_interval": 1000,
    "mb_enabled": False,
    "mb_port": 502,
    "mb_unit_id": 1,
}

# Not in the original Config struct at all (the firmware's OTA password was a
# hardcoded C constant) - this whole section is new for the git-pull-based
# update mechanism. See app/updater.py. token/ssh_key_set are booleans only;
# the actual secrets are never read back out through the API once saved.
DEFAULT_UPDATE = {
    "update_password": "123456",
    "repo_url": "",
    "branch": "main",
    "auth_method": "none",  # none | token | ssh_key
    "token": "",
}

DEFAULT_CHANNELS = {
    "di": [{"label": f"Input {i + 1}", "mode": 0, "invert": False} for i in range(NUM_DI)],
    "do": [{"label": f"Relay {i}", "mode": 0, "invert": False} for i in range(NUM_DO)],
    "counts": [0] * NUM_DI,
}

DEFAULT_RS485 = {
    "enabled": False,
    "baud": 9600,
    "parity": 0,
    "data_bits": 8,
    "stop_bits": RS485_STOP_BITS_1,
    "slaves": [
        {
            "enabled": True,
            "slave_id": 1,
            "poll_interval_ms": 2000,
            "label": "Slave 1",
            "registers": [
                {
                    "reg_type": RS485_REG_HOLDING,
                    "start_addr": 0,
                    "data_type": RS485_DT_U16,
                    "label": "Reg 1",
                    "scale": 1.0,
                    "unit": "",
                }
            ],
        }
    ],
}

_locks = {
    "system.json": threading.Lock(),
    "channels.json": threading.Lock(),
    "rs485.json": threading.Lock(),
    "update.json": threading.Lock(),
}


def _path(name):
    return os.path.join(DATA_DIR, name)


def _load_raw(name, default):
    p = _path(name)
    if not os.path.exists(p):
        return json.loads(json.dumps(default))  # deep copy
    try:
        with open(p, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(default))
    merged = json.loads(json.dumps(default))
    merged.update(data)
    return merged


def _atomic_write(name, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    p = _path(name)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, p)


def _is_bool(v):
    return isinstance(v, bool)


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_str(v):
    return isinstance(v, str)


def _truncate(s, max_len):
    return s[: max_len - 1] if s is not None else ""


# ---------------------------------------------------------------------------
# system.json — Config (network + MQTT + Modbus TCP)
# ---------------------------------------------------------------------------

def load_config():
    """Full Config, defaults applied for anything missing/invalid on disk."""
    with _locks["system.json"]:
        return _load_raw("system.json", DEFAULT_SYSTEM)


def save_wifi_config(body):
    with _locks["system.json"]:
        cfg = _load_raw("system.json", DEFAULT_SYSTEM)
        if _is_str(body.get("ssid")):
            cfg["wifi_ssid"] = body["ssid"]
        if _is_str(body.get("password")):
            cfg["wifi_pass"] = body["password"]
        if _is_str(body.get("ip")):
            cfg["wifi_ip"] = body["ip"]
        subnet = body.get("subnet")
        if _is_str(subnet) and subnet != "":
            cfg["wifi_subnet"] = subnet
        if _is_str(body.get("gateway")):
            cfg["wifi_gateway"] = body["gateway"]
        if _is_str(body.get("dns")):
            cfg["wifi_dns"] = body["dns"]
        # "mode" is always resolved and always written, defaulting to static
        # if absent/unrecognized - matches save_wifi_config()'s unconditional write.
        mode = body.get("mode")
        cfg["wifi_static"] = (mode == "static") if _is_str(mode) else True
        if _is_bool(body.get("wifi_enabled")):
            cfg["wifi_enabled"] = body["wifi_enabled"]
        _atomic_write("system.json", cfg)
        return cfg


def save_lan_config(body):
    with _locks["system.json"]:
        cfg = _load_raw("system.json", DEFAULT_SYSTEM)
        if _is_str(body.get("ip")):
            cfg["lan_ip"] = body["ip"]
        subnet = body.get("subnet")
        if _is_str(subnet) and subnet != "":
            cfg["lan_subnet"] = subnet
        if _is_str(body.get("gateway")):
            cfg["lan_gateway"] = body["gateway"]
        if _is_str(body.get("dns")):
            cfg["lan_dns"] = body["dns"]
        mode = body.get("mode")
        cfg["lan_static"] = (mode == "static") if _is_str(mode) else True
        _atomic_write("system.json", cfg)
        return cfg


def save_mqtt_config(body):
    with _locks["system.json"]:
        cfg = _load_raw("system.json", DEFAULT_SYSTEM)
        if _is_bool(body.get("enabled")):
            cfg["mqtt_enabled"] = body["enabled"]
        if _is_str(body.get("broker")):
            cfg["mqtt_broker"] = body["broker"]
        port = body.get("port")
        if _is_number(port) and 0 < port <= 65535:
            cfg["mqtt_port"] = int(port)
        if _is_str(body.get("topic")):
            cfg["mqtt_topic"] = body["topic"]
        interval = body.get("interval")
        if _is_number(interval) and interval > 0:
            cfg["mqtt_interval"] = int(interval)
        _atomic_write("system.json", cfg)
        return cfg


def save_modbus_config(body):
    with _locks["system.json"]:
        cfg = _load_raw("system.json", DEFAULT_SYSTEM)
        if _is_bool(body.get("enabled")):
            cfg["mb_enabled"] = body["enabled"]
        port = body.get("port")
        if _is_number(port) and 0 < port <= 65535:
            cfg["mb_port"] = int(port)
        unit_id = body.get("unit_id")
        if _is_number(unit_id) and 1 <= unit_id <= 247:
            cfg["mb_unit_id"] = int(unit_id)
        _atomic_write("system.json", cfg)
        return cfg


# ---------------------------------------------------------------------------
# channels.json — ChannelConfig (DI/DO labels/modes/invert) + DI counters
# ---------------------------------------------------------------------------

def load_channel_config():
    with _locks["channels.json"]:
        data = _load_raw("channels.json", DEFAULT_CHANNELS)
        return {"di": data["di"], "do": data["do"]}


def save_channel_config(body):
    with _locks["channels.json"]:
        data = _load_raw("channels.json", DEFAULT_CHANNELS)
        di_in = body.get("di")
        if isinstance(di_in, list):
            for i, item in enumerate(di_in[:NUM_DI]):
                if not isinstance(item, dict):
                    continue
                if _is_str(item.get("label")):
                    data["di"][i]["label"] = _truncate(item["label"], CH_LABEL_LEN)
                if _is_number(item.get("mode")):
                    data["di"][i]["mode"] = int(item["mode"])
                if _is_bool(item.get("invert")):
                    data["di"][i]["invert"] = item["invert"]
        do_in = body.get("do")
        if isinstance(do_in, list):
            for i, item in enumerate(do_in[:NUM_DO]):
                if not isinstance(item, dict):
                    continue
                if _is_str(item.get("label")):
                    data["do"][i]["label"] = _truncate(item["label"], CH_LABEL_LEN)
                if _is_number(item.get("mode")):
                    data["do"][i]["mode"] = int(item["mode"])
                if _is_bool(item.get("invert")):
                    data["do"][i]["invert"] = item["invert"]
        _atomic_write("channels.json", data)
        return {"di": data["di"], "do": data["do"]}


def load_counts():
    with _locks["channels.json"]:
        data = _load_raw("channels.json", DEFAULT_CHANNELS)
        counts = data.get("counts", [0] * NUM_DI)
        return (counts + [0] * NUM_DI)[:NUM_DI]


def save_counts(counts):
    with _locks["channels.json"]:
        data = _load_raw("channels.json", DEFAULT_CHANNELS)
        data["counts"] = list(counts[:NUM_DI])
        _atomic_write("channels.json", data)


# ---------------------------------------------------------------------------
# rs485.json — Rs485Config (bus settings + dynamic slave/register list)
# ---------------------------------------------------------------------------

def load_rs485_config():
    with _locks["rs485.json"]:
        return _load_raw("rs485.json", DEFAULT_RS485)


def _validate_register(reg_in):
    reg_type = reg_in.get("reg_type")
    if not (_is_number(reg_type) and RS485_REG_HOLDING <= reg_type <= RS485_REG_DISCRETE):
        reg_type = RS485_REG_HOLDING
    start_addr = reg_in.get("start_addr")
    if not (_is_number(start_addr) and 0 <= start_addr <= 0xFFFF):
        start_addr = 0
    data_type = reg_in.get("data_type")
    if not (_is_number(data_type) and RS485_DT_U16 <= data_type <= RS485_DT_BIT):
        data_type = RS485_DT_U16
    label = reg_in.get("label")
    label = _truncate(label, RS485_LABEL_LEN) if _is_str(label) else ""
    scale = reg_in.get("scale")
    scale = float(scale) if _is_number(scale) else 1.0
    unit = reg_in.get("unit")
    unit = _truncate(unit, RS485_UNIT_LEN) if _is_str(unit) else ""
    return {
        "reg_type": int(reg_type),
        "start_addr": int(start_addr),
        "data_type": int(data_type),
        "label": label,
        "scale": scale,
        "unit": unit,
    }


def save_rs485_config(body):
    with _locks["rs485.json"]:
        cfg = _load_raw("rs485.json", DEFAULT_RS485)
        if _is_bool(body.get("enabled")):
            cfg["enabled"] = body["enabled"]
        baud = body.get("baud")
        if _is_number(baud) and baud > 0:
            cfg["baud"] = int(baud)
        parity = body.get("parity")
        if _is_number(parity) and 0 <= parity <= 2:
            cfg["parity"] = int(parity)
        data_bits = body.get("data_bits")
        if _is_number(data_bits) and 5 <= data_bits <= 8:
            cfg["data_bits"] = int(data_bits)
        stop_bits = body.get("stop_bits")
        if _is_number(stop_bits) and 0 <= stop_bits <= RS485_STOP_BITS_2:
            cfg["stop_bits"] = int(stop_bits)

        slaves_in = body.get("slaves")
        if isinstance(slaves_in, list):
            # Wholesale replace, capped at RS485_MAX_SLAVES. Unlike the firmware's
            # fixed-size NVS slot array (which needed an explicit "clear stale
            # slots beyond the new count" step to avoid ghost slaves - a real bug
            # fixed once already, see memory/RS485_MODBUS_RTU_CONTEX.md), a plain
            # JSON list has no leftover slots by construction: this replacement
            # IS the clear.
            new_slaves = []
            for s_in in slaves_in[:RS485_MAX_SLAVES]:
                if not isinstance(s_in, dict):
                    continue
                enabled = s_in.get("enabled")
                enabled = enabled if _is_bool(enabled) else True
                slave_id = s_in.get("slave_id")
                slave_id = int(slave_id) if (_is_number(slave_id) and 1 <= slave_id <= 247) else 1
                poll_ms = s_in.get("poll_interval_ms")
                poll_ms = int(poll_ms) if (_is_number(poll_ms) and poll_ms > 0) else 2000
                label = s_in.get("label")
                label = _truncate(label, RS485_LABEL_LEN) if _is_str(label) else ""

                regs_in = s_in.get("registers")
                registers = []
                if isinstance(regs_in, list):
                    for r_in in regs_in[:RS485_MAX_REGS_PER_SLAVE]:
                        if isinstance(r_in, dict):
                            registers.append(_validate_register(r_in))

                new_slaves.append(
                    {
                        "enabled": enabled,
                        "slave_id": slave_id,
                        "poll_interval_ms": poll_ms,
                        "label": label,
                        "registers": registers,
                    }
                )
            cfg["slaves"] = new_slaves

        _atomic_write("rs485.json", cfg)
        return cfg


# ---------------------------------------------------------------------------
# update.json - git remote/branch/credentials for the /update pull mechanism
# ---------------------------------------------------------------------------

_VALID_AUTH_METHODS = ("none", "token", "ssh_key")


def load_update_config():
    """Full stored config, secrets included - internal use (updater.py) only.
    Callers building an API response must use redact_update_config() instead."""
    with _locks["update.json"]:
        return _load_raw("update.json", DEFAULT_UPDATE)


def redact_update_config(cfg=None):
    """Same shape, but token/update_password replaced with presence booleans -
    same never-round-trip-a-secret spirit as wifi_pass in app_js.cpp."""
    if cfg is None:
        cfg = load_update_config()
    return {
        "repo_url": cfg.get("repo_url", ""),
        "branch": cfg.get("branch", "main"),
        "auth_method": cfg.get("auth_method", "none"),
        "token_set": bool(cfg.get("token")),
        "password_set": bool(cfg.get("update_password")),
    }


def save_update_config(body):
    with _locks["update.json"]:
        cfg = _load_raw("update.json", DEFAULT_UPDATE)
        if _is_str(body.get("repo_url")):
            cfg["repo_url"] = body["repo_url"].strip()
        if _is_str(body.get("branch")) and body["branch"].strip():
            cfg["branch"] = body["branch"].strip()
        auth_method = body.get("auth_method")
        if _is_str(auth_method) and auth_method in _VALID_AUTH_METHODS:
            cfg["auth_method"] = auth_method
        # Secrets: only overwritten when a non-empty value is actually sent -
        # an empty/absent field means "leave the stored one alone", same
        # partial-update rule as everywhere else, so the UI never needs to
        # re-send a token/password just to change the branch.
        if _is_str(body.get("token")) and body["token"] != "":
            cfg["token"] = body["token"]
        if _is_str(body.get("update_password")) and body["update_password"] != "":
            cfg["update_password"] = body["update_password"]
        _atomic_write("update.json", cfg)
        return cfg
