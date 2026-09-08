"""Relay outputs + digital input scan/counters. Port of modules/gpio.c.

GPIO numbers are Pi BCM numbers for the SAME physical header pins the ESP32-P4
firmware used - re-derived from the board schematic this session, NOT copied
from gpio.c's ESP32 GPIO numbers (those are meaningless on a Pi). See the plan
file / memory/rpi4_esp32p4_gpio_reference.md for the pin-by-pin cross-check.
"""
import logging
import threading
import time

from app import config, state

log = logging.getLogger("gpio")

try:
    import RPi.GPIO as GPIO

    _HAVE_GPIO = True
except (ImportError, RuntimeError):
    log.warning("RPi.GPIO not available - running with a no-op GPIO stub (dev/off-Pi mode)")
    _HAVE_GPIO = False

    class _StubGPIO:
        BCM = OUT = IN = PUD_UP = HIGH = LOW = None

        def setmode(self, *a, **k):
            pass

        def setup(self, *a, **k):
            pass

        def output(self, *a, **k):
            pass

        def input(self, *a, **k):
            return 1  # idle high = inactive, matching pulled-up/inactive-open input

    GPIO = _StubGPIO()

NUM_RELAYS = config.NUM_DO
NUM_INPUTS = config.NUM_DI

DI_MODE_NORMAL = 0
DI_MODE_COUNTER = 1
DI_MODE_FREQUENCY = 2

# DO0 = relay 0 / RLY1, DO1 = relay 1 / RLY2 - header pins 15, 13
DO_PINS = [22, 27]
# DI0..DI9 - header pins 36,35,38,37,40,22,29,32,31,33
DI_PINS = [16, 19, 20, 26, 21, 25, 5, 12, 6, 13]

_lock = threading.Lock()
_relay_states = [False] * NUM_RELAYS
_input_states = [False] * NUM_INPUTS
_di_modes = [DI_MODE_NORMAL] * NUM_INPUTS
_di_invert = [False] * NUM_INPUTS
_di_counts = [0] * NUM_INPUTS
_counts_dirty = False

_SCAN_PERIOD_S = 0.05
_COUNT_SAVE_PERIOD_S = 10.0


def gpio_init():
    GPIO.setmode(GPIO.BCM)
    for pin in DO_PINS:
        GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)
    for pin in DI_PINS:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)


def set_di_mode(ch, mode):
    if 0 <= ch < NUM_INPUTS:
        with _lock:
            _di_modes[ch] = mode


def get_di_mode(ch):
    if 0 <= ch < NUM_INPUTS:
        return _di_modes[ch]
    return DI_MODE_NORMAL


def set_di_invert(ch, invert):
    if 0 <= ch < NUM_INPUTS:
        with _lock:
            _di_invert[ch] = bool(invert)


def get_counts():
    with _lock:
        return list(_di_counts)


def reset_count(ch):
    global _counts_dirty
    with _lock:
        if ch == -1:
            for i in range(NUM_INPUTS):
                _di_counts[i] = 0
        elif 0 <= ch < NUM_INPUTS:
            _di_counts[ch] = 0
        counts = list(_di_counts)
    config.save_counts(counts)
    _counts_dirty = False
    state.broadcast()


def set_relay(relay_id, on):
    if not (0 <= relay_id < NUM_RELAYS):
        log.warning("invalid relay id %s", relay_id)
        return False
    with _lock:
        _relay_states[relay_id] = bool(on)
    GPIO.output(DO_PINS[relay_id], GPIO.HIGH if on else GPIO.LOW)
    state.broadcast()
    return True


def get_relay_states():
    with _lock:
        return list(_relay_states)


def get_input_states():
    with _lock:
        return list(_input_states)


def _scan_loop():
    prev = [False] * NUM_INPUTS
    global _counts_dirty
    while True:
        changed = False
        with _lock:
            for i in range(NUM_INPUTS):
                # physical LOW = logical HIGH (active), pull-up idle-high; XOR invert
                val = (GPIO.input(DI_PINS[i]) == 0) ^ _di_invert[i]
                if val != prev[i]:
                    if val and _di_modes[i] == DI_MODE_COUNTER:
                        _di_counts[i] += 1
                        _counts_dirty = True
                    prev[i] = val
                    changed = True
                _input_states[i] = val
        if changed:
            state.broadcast()
        time.sleep(_SCAN_PERIOD_S)


def _count_save_loop():
    global _counts_dirty
    while True:
        time.sleep(_COUNT_SAVE_PERIOD_S)
        if _counts_dirty:
            with _lock:
                counts = list(_di_counts)
            config.save_counts(counts)
            _counts_dirty = False


def start_input_scan():
    with _lock:
        loaded = config.load_counts()
        for i in range(NUM_INPUTS):
            _di_counts[i] = loaded[i]
    threading.Thread(target=_scan_loop, name="gpio-scan", daemon=True).start()
    threading.Thread(target=_count_save_loop, name="gpio-cnt-save", daemon=True).start()
