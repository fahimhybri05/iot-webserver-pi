"""RS485 Modbus RTU master. Port of modules/modbus_rtu_manager.c.

DE (GPIO23) is a plain GPIO, not the UART's hardware RTS line, so - unlike
the ESP32-P4 firmware, whose UART peripheral could remap ANY pin to act as
hardware-driven DE via UART_MODE_RS485_HALF_DUPLEX - there's no kernel/driver
auto-toggle available for this specific pin assignment on the Pi (true on
both Pi 4's BCM2711 and Pi 5's RP1 - neither exposes hardware RS485 DE
framing the way the ESP32 UART peripheral does). DE is toggled manually
around each transaction instead: high -> write -> flush (block until the OS
write buffer is actually on the wire) -> small settle delay -> low. nRE
(GPIO24) is fixed low once at startup and never toggled, same as the
firmware (the transceiver's receiver stays enabled permanently; half-duplex
framing alone prevents self-echo confusion since we only read after our own
write is confirmed flushed).

GPIO access goes through the `rpi-lgpio` package (imported as `RPi.GPIO`,
see gpio_driver.py's docstring) so this same code runs unmodified on Pi 4
and Pi 5 - real RPi.GPIO would fail outright on the Pi 5's RP1 chip.

Hand-rolled RTU framing (CRC16, FC01/02/03/04 read-only) rather than a
library, so the decode logic can mirror modbus_rtu_manager.c's traced
behavior exactly: a single coil/discrete read's value is always bit 0 of the
response data byte (true by construction for a quantity=1 read, not an
esp-modbus quirk), and multi-word values assume big-endian WORD order (first
register = high word) on top of each register's own big-endian byte order
per the Modbus wire format.
"""
import logging
import struct
import threading
import time

log = logging.getLogger("modbus_rtu")

try:
    import serial

    _HAVE_SERIAL = True
except ImportError:
    log.warning("pyserial not installed - RS485 Modbus RTU master disabled")
    _HAVE_SERIAL = False

try:
    import RPi.GPIO as GPIO

    _HAVE_GPIO = True
except (ImportError, RuntimeError):
    _HAVE_GPIO = False

    class _StubGPIO:
        BCM = OUT = HIGH = LOW = None

        def setmode(self, *a, **k):
            pass

        def setup(self, *a, **k):
            pass

        def output(self, *a, **k):
            pass

    GPIO = _StubGPIO()

# Header pins 16 (DE) / 18 (nRE) -> Pi BCM GPIO23 / GPIO24 (see plan/GPIO table).
RTU_DE_GPIO = 23
RTU_NRE_GPIO = 24
RTU_SERIAL_PORT = "/dev/serial0"  # header pins 8/10 -> Pi hw UART0 (GPIO14 TXD/GPIO15 RXD)

# Same 300ms floor the firmware set (CONFIG_FMB_MASTER_TIMEOUT_MS_RESPOND) so one
# dead slave/register can't stall a multi-slave poll round for seconds.
RTU_RESPONSE_TIMEOUT_S = 0.3
RTU_POLL_TICK_S = 0.02
RTU_DE_SETTLE_S = 0.002
# Modbus RTU's minimum inter-frame silence: >=3.5 character times so a
# receiver's idle-time frame-boundary detector actually resets between
# requests (about 4ms at 9600 8N1: 11 bits/char / 9600 baud * 3.5). Without
# this, a slave that replies quickly (or rejects fast) can get the next
# request before its receiver's silence window elapsed, and the two frames
# arrive as one unbroken run of bytes from the receiver's point of view -
# looks exactly like "CRC error, N*8 bytes" on the slave/simulator side even
# though every individual request was well-formed.
RTU_INTERFRAME_GAP_S = 0.004

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

_FUNC_BY_REG_TYPE = {
    RS485_REG_INPUT: 0x04,
    RS485_REG_COIL: 0x01,
    RS485_REG_DISCRETE: 0x02,
    RS485_REG_HOLDING: 0x03,
}

_lock = threading.Lock()
_status = []  # list of dicts, index-aligned with cfg["slaves"], same shape as RtuSlaveStatus
_running = False


def is_running():
    return _running


def get_status_json():
    """[{id,label,registers:[{label,value,unit}],online,last_update_ms}, ...],
    skipping unconfigured slots - same shape as the modbus_rtu status JSON
    the firmware builds in http_server_build_status_json()."""
    with _lock:
        out = []
        for st in _status:
            if not st.get("configured"):
                continue
            registers = [
                {"label": st["reg_labels"][j], "value": st["values"][j], "unit": st["reg_units"][j]}
                for j in range(st["reg_count"])
            ]
            out.append(
                {
                    "id": st["slave_id"],
                    "label": st["label"],
                    "registers": registers,
                    "online": st["online"],
                    "last_update_ms": st["last_update_ms"],
                }
            )
        return out


def _crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def _build_request(slave_id, func_code, start_addr, quantity):
    body = struct.pack(">BBHH", slave_id, func_code, start_addr, quantity)
    return body + struct.pack("<H", _crc16(body))


def _bits_const(n):
    return {5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS}.get(n, serial.EIGHTBITS)


def _parity_const(p):
    return {1: serial.PARITY_ODD, 2: serial.PARITY_EVEN}.get(p, serial.PARITY_NONE)


def _stopbits_const(s):
    return {1: serial.STOPBITS_ONE_POINT_FIVE, 2: serial.STOPBITS_TWO}.get(s, serial.STOPBITS_ONE)


_last_tx_end = 0.0


def _transact(ser, slave_id, func_code, start_addr, quantity):
    global _last_tx_end

    # Enforce the minimum inter-frame silence (RTU_INTERFRAME_GAP_S) before
    # this request starts, measured from the end of whatever we last put on
    # the wire - see that constant's comment for why. Only the single
    # rtu-poll thread ever calls _transact, so this plain module global
    # needs no lock.
    gap = RTU_INTERFRAME_GAP_S - (time.monotonic() - _last_tx_end)
    if gap > 0:
        time.sleep(gap)

    req = _build_request(slave_id, func_code, start_addr, quantity)
    ser.reset_input_buffer()

    GPIO.output(RTU_DE_GPIO, GPIO.HIGH)
    # Let the transceiver actually finish switching to drive mode before the
    # first bit goes out - skipping this risks clipping the frame's leading
    # byte(s) on the wire, which looks like a truncated/garbled response on
    # the receiving end even though we transmitted the full request.
    time.sleep(RTU_DE_SETTLE_S)
    try:
        ser.write(req)
        ser.flush()  # block until the request is actually on the wire
    finally:
        time.sleep(RTU_DE_SETTLE_S)
        GPIO.output(RTU_DE_GPIO, GPIO.LOW)
        _last_tx_end = time.monotonic()

    # nRE is tied permanently LOW (receiver always enabled - see module
    # docstring), including during our own transmission. Unlike the ESP32
    # firmware's dedicated UART_MODE_RS485_HALF_DUPLEX hardware feature
    # (which suppresses self-reception in silicon), this Pi UART has no such
    # feature: on a shared differential pair, an always-on receiver sees our
    # own outgoing bytes just as much as an incoming reply, and they land in
    # the OS receive buffer over the same ~4.6ms (at 19200 baud) it took to
    # transmit them. Discard that self-echo now, after our own transmission
    # has fully finished, so the next ser.read() below can only pick up the
    # slave's real reply - not our own request misread as one (which is
    # indistinguishable from "CRC mismatch" garbage: our own request's
    # address/function bytes happen to be exactly what we just sent, and the
    # rest of the frame gets misparsed as a bogus length + bogus CRC).
    ser.reset_input_buffer()

    header = ser.read(2)
    if len(header) < 2:
        raise TimeoutError(f"no response (got {header.hex()})")
    resp_addr, resp_func = header[0], header[1]
    if resp_func & 0x80:
        rest = ser.read(3)
        code = rest[0] if rest else -1
        raise IOError(f"slave {slave_id} exception response, code={code}")

    bc = ser.read(1)
    if len(bc) < 1:
        raise TimeoutError(f"short response (no byte count) header={header.hex()}")
    byte_count = bc[0]
    data = ser.read(byte_count)
    crc_bytes = ser.read(2)
    if len(data) < byte_count or len(crc_bytes) < 2:
        raise TimeoutError(
            f"incomplete response: expected {byte_count} data bytes, got {len(data)} "
            f"- header={header.hex()} bc={bc.hex()} data={data.hex()} crc_read={crc_bytes.hex()}"
        )

    frame = header + bc + data
    expected_crc = _crc16(frame)
    got_crc = struct.unpack("<H", crc_bytes)[0]
    if got_crc != expected_crc:
        raise IOError(
            f"CRC mismatch: frame={frame.hex()} got_crc={got_crc:04x} expected_crc={expected_crc:04x}"
        )
    if resp_addr != slave_id or resp_func != func_code:
        raise IOError(f"unexpected addr/func in response: {resp_addr}/{resp_func} raw={frame.hex()}")
    return data


def _decode(reg_type, data_type, scale, data):
    if reg_type in (RS485_REG_COIL, RS485_REG_DISCRETE):
        # Quantity=1 bit read - always bit 0 of the single data byte.
        return (data[0] & 0x01) * scale if data else 0.0

    if data_type in (RS485_DT_U32, RS485_DT_S32, RS485_DT_F32):
        raw0, raw1 = struct.unpack(">HH", data[:4])
        combined = (raw0 << 16) | raw1  # big-endian WORD order: first reg = high word
        if data_type == RS485_DT_U32:
            return combined * scale
        if data_type == RS485_DT_S32:
            if combined & 0x80000000:
                combined -= 1 << 32
            return combined * scale
        (f,) = struct.unpack(">f", struct.pack(">I", combined))
        return f * scale

    if data_type == RS485_DT_S16:
        (raw,) = struct.unpack(">h", data[:2])
        return raw * scale
    (raw,) = struct.unpack(">H", data[:2])
    return raw * scale


def _poll_loop(cfg):
    from app import state

    try:
        ser = serial.Serial(
            port=RTU_SERIAL_PORT,
            baudrate=cfg.get("baud", 9600) or 9600,
            bytesize=_bits_const(cfg.get("data_bits", 8)),
            parity=_parity_const(cfg.get("parity", 0)),
            stopbits=_stopbits_const(cfg.get("stop_bits", 0)),
            timeout=RTU_RESPONSE_TIMEOUT_S,
        )
    except Exception as e:
        log.error("failed to open %s: %s", RTU_SERIAL_PORT, e)
        return

    global _running
    _running = True
    slaves = cfg.get("slaves", [])
    last_poll_ms = [0.0] * len(slaves)
    log.info(
        "RS485 Modbus RTU master up: baud=%s data_bits=%s stop_bits=%s parity=%s slaves=%s",
        cfg.get("baud"), cfg.get("data_bits"), cfg.get("stop_bits"), cfg.get("parity"), len(slaves),
    )

    while True:
        now_ms = time.monotonic() * 1000
        polled_any = False

        for i, sc in enumerate(slaves):
            if not sc.get("enabled"):
                continue
            if now_ms - last_poll_ms[i] < sc.get("poll_interval_ms", 2000):
                continue
            last_poll_ms[i] = now_ms
            polled_any = True

            regs = sc.get("registers", [])
            round_ok = len(regs) > 0
            decoded = [0.0] * len(regs)

            for j, rc in enumerate(regs):
                reg_type = rc.get("reg_type", RS485_REG_HOLDING)
                data_type = rc.get("data_type", RS485_DT_U16)
                is_bit = reg_type in (RS485_REG_COIL, RS485_REG_DISCRETE)
                wide = (not is_bit) and data_type in (RS485_DT_U32, RS485_DT_S32, RS485_DT_F32)
                func = _FUNC_BY_REG_TYPE.get(reg_type, 0x03)
                qty = 1 if is_bit else (2 if wide else 1)
                try:
                    data = _transact(ser, sc["slave_id"], func, rc.get("start_addr", 0), qty)
                    decoded[j] = _decode(reg_type, data_type, rc.get("scale", 1.0), data)
                except Exception as e:
                    round_ok = False
                    log.warning("slave %s reg %d poll failed: %s", sc.get("slave_id"), j, e)

            with _lock:
                st = _status[i]
                if round_ok:
                    st["reg_count"] = len(regs)
                    st["values"] = decoded
                    st["online"] = True
                    st["last_update_ms"] = now_ms
                elif now_ms - st["last_update_ms"] > sc.get("poll_interval_ms", 2000) * 3:
                    st["online"] = False

        if polled_any:
            state.broadcast()
        time.sleep(RTU_POLL_TICK_S)


def init(cfg):
    """No-op if disabled - mirrors modbus_rtu_manager_init()'s skip/never-abort."""
    global _status
    if not cfg.get("enabled"):
        log.info("RS485 Modbus RTU master disabled - skipping init")
        return
    if not _HAVE_SERIAL:
        return

    slaves = cfg.get("slaves", [])
    _status = []
    for sc in slaves:
        regs = sc.get("registers", [])
        _status.append(
            {
                "configured": bool(sc.get("enabled")),
                "slave_id": sc.get("slave_id", 1),
                "label": sc.get("label", ""),
                "reg_count": len(regs),
                "reg_labels": [r.get("label", "") for r in regs],
                "reg_units": [r.get("unit", "") for r in regs],
                "values": [0.0] * len(regs),
                "online": False,
                "last_update_ms": 0.0,
            }
        )

    GPIO.setmode(GPIO.BCM)
    GPIO.setup(RTU_DE_GPIO, GPIO.OUT, initial=GPIO.LOW)
    GPIO.setup(RTU_NRE_GPIO, GPIO.OUT, initial=GPIO.LOW)  # receiver always enabled

    threading.Thread(target=_poll_loop, args=(cfg,), name="rtu-poll", daemon=True).start()
