"""Modbus TCP slave, read-only. Port of modules/modbus_manager.c.

Register map (unchanged from firmware, see memory/PROTOCOLS.md):
  Discrete inputs (FC02) 0-9  = DI0..DI9
  Discrete inputs (FC02) 10-11 = relay DO0, DO1
  Input registers (FC04) 0-19 = 10x 32-bit DI counters, low word first
    counter i -> regs[2*i] (low), regs[2*i+1] (high)

Coils and holding registers are intentionally left un-addressable (via
_NoAccessBlock below) so FC01/03/05/06/15/16 all return an exception -
mirrors the original "no writable areas, master cannot actuate anything"
design, and matches PROTOCOLS.md's documented "coils/holding not present ->
exception" behavior exactly, regardless of pymodbus's own defaults for an
unconfigured store.

Tested against pymodbus 3.x's ModbusSlaveContext.setValues(fx, address, values)
API - if a different major version is installed, check that signature first.
"""
import logging
import threading
import time

log = logging.getLogger("modbus_tcp")

try:
    from pymodbus.datastore import (
        ModbusSequentialDataBlock,
        ModbusServerContext,
        ModbusSlaveContext,
    )
    from pymodbus.server import StartTcpServer

    _HAVE_PYMODBUS = True
except ImportError:
    log.warning("pymodbus not installed - Modbus TCP slave disabled")
    _HAVE_PYMODBUS = False

NUM_INPUTS = 10
NUM_RELAYS = 2
MB_DISCRETE_BITS = NUM_INPUTS + NUM_RELAYS  # 12
MB_INPUT_REGS = NUM_INPUTS * 2  # 20, 32-bit counters

_SYNC_PERIOD_S = 0.05

_running = False
_context = None
_unit_id = 1


def is_running():
    return _running


if _HAVE_PYMODBUS:

    class _NoAccessBlock(ModbusSequentialDataBlock):
        """Always rejects - used for coils/holding so writes (and reads) of
        an area the firmware never exposed hit an exception, not zeros."""

        def __init__(self):
            super().__init__(0, [0])

        def validate(self, address, count=1):
            return False


def _sync_loop():
    from app import gpio_driver

    while True:
        time.sleep(_SYNC_PERIOD_S)
        di = gpio_driver.get_input_states()
        rel = gpio_driver.get_relay_states()
        cnt = gpio_driver.get_counts()
        bits = [bool(v) for v in di] + [bool(v) for v in rel]
        regs = [0] * MB_INPUT_REGS
        for i, c in enumerate(cnt):
            regs[2 * i] = c & 0xFFFF
            regs[2 * i + 1] = (c >> 16) & 0xFFFF
        try:
            slave_ctx = _context[_unit_id]
            slave_ctx.setValues(2, 0, bits)  # FC02 discrete inputs
            slave_ctx.setValues(4, 0, regs)  # FC04 input registers
        except Exception as e:
            log.warning("register sync failed: %s", e)


def _serve(port):
    global _running
    try:
        _running = True
        log.info("Modbus TCP slave up: port=%s uid=%s", port, _unit_id)
        StartTcpServer(context=_context, address=("0.0.0.0", port))
    except Exception as e:
        log.error("Modbus TCP server failed: %s", e)
    finally:
        _running = False


def init(cfg):
    """No-op if disabled - mirrors modbus_manager_init()'s skip/never-abort."""
    global _context, _unit_id
    if not cfg.get("mb_enabled"):
        log.info("Modbus TCP disabled - skipping init")
        return
    if not _HAVE_PYMODBUS:
        return

    port = cfg.get("mb_port") or 502
    _unit_id = cfg.get("mb_unit_id") or 1

    di_block = ModbusSequentialDataBlock(0, [0] * MB_DISCRETE_BITS)
    ir_block = ModbusSequentialDataBlock(0, [0] * MB_INPUT_REGS)
    slave_ctx = ModbusSlaveContext(
        di=di_block,
        co=_NoAccessBlock(),
        hr=_NoAccessBlock(),
        ir=ir_block,
        zero_mode=True,
    )
    _context = ModbusServerContext(slaves={_unit_id: slave_ctx}, single=False)

    threading.Thread(target=_sync_loop, name="mb-tcp-sync", daemon=True).start()
    threading.Thread(target=_serve, args=(port,), name="mb-tcp-server", daemon=True).start()
