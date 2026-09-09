"""SSD1306 OLED - shows the device's Ethernet IP. Port of modules/oled_display.c.

I2C SDA/SCL -> header pins 3/5 -> Pi hw I2C1 (GPIO2/GPIO3), bus 1, addr 0x3C.
Same behavior as the firmware: poll the IP every 2s, only redraw when it
actually changed (avoid needless I2C traffic), no special handling for
"no IP yet" beyond showing whatever network_manager.get_lan_ip() returns
(firmware showed "0.0.0.0" pre-DHCP - our get_lan_ip() already returns that
same string when nmcli reports no address).
"""
import logging
import threading
import time

log = logging.getLogger("oled")

try:
    from luma.core.interface.serial import i2c
    from luma.oled.device import ssd1306
    from PIL import Image, ImageDraw

    _HAVE_LUMA = True
except ImportError:
    log.warning("luma.oled not installed - OLED display disabled")
    _HAVE_LUMA = False

_POLL_PERIOD_S = 2.0


def _display_loop(device):
    from app import network_manager

    last_ip = None
    while True:
        try:
            ip = network_manager.get_lan_ip()
            if ip != last_ip:
                last_ip = ip
                with Image.new("1", device.size) as img:
                    draw = ImageDraw.Draw(img)
                    draw.text((0, 24), ip, fill=255)
                    device.display(img)
        except Exception:
            # A transient I2C error here shouldn't permanently kill IP
            # display for the rest of the process's life - same
            # never-abort spirit as init()'s own failure handling below.
            log.exception("OLED display loop error - continuing")
        time.sleep(_POLL_PERIOD_S)


def init():
    """Logs + returns on any failure, never aborts startup - same pattern as
    every other optional module (oled_display_init() in the firmware)."""
    if not _HAVE_LUMA:
        return
    try:
        serial_iface = i2c(port=1, address=0x3C)
        device = ssd1306(serial_iface)
    except Exception as e:
        log.warning("SSD1306 not responding - skipping OLED (%s)", e)
        return

    threading.Thread(target=_display_loop, args=(device,), name="oled", daemon=True).start()
