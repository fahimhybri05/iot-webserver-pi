"""Entrypoint. Port of main.c's app_main().

Same init order as the firmware: config -> gpio -> network/wifi -> mqtt ->
modbus tcp -> rs485 -> oled -> apply saved channel config -> serve. (The C
version calls http_server_start() earlier since it's non-blocking there;
here Flask's app.run() is the blocking call, so it's kept last while every
other module still runs in its own background thread, same net effect.)

sd_notify.ready() fires right before that final blocking call - reaching it
means every peripheral init above already returned (they're all
individually failure-tolerant, so this is a meaningful "actually booted",
not just "the process exists"). No firmware equivalent - the ESP32 has no
supervisor process to notify.
"""
import logging

from app import (
    config,
    gpio_driver,
    http_server,
    modbus_rtu_master,
    modbus_tcp_slave,
    mqtt_manager,
    network_manager,
    oled_display,
    sd_notify,
)


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    log = logging.getLogger("main")

    sd_notify.start_watchdog()  # no-op unless the systemd unit sets WatchdogSec=

    sys_cfg = config.load_config()

    gpio_driver.gpio_init()
    gpio_driver.start_input_scan()

    network_manager.init(sys_cfg)
    mqtt_manager.init(sys_cfg)
    modbus_tcp_slave.init(sys_cfg)

    rs485_cfg = config.load_rs485_config()
    modbus_rtu_master.init(rs485_cfg)

    oled_display.init()

    # Apply saved DI modes + invert flags, same as main.c's post-init pass.
    ch_cfg = config.load_channel_config()
    for i, ch in enumerate(ch_cfg["di"]):
        gpio_driver.set_di_mode(i, ch.get("mode", 0))
        gpio_driver.set_di_invert(i, ch.get("invert", False))

    log.info("PL Connect (Raspberry Pi port) initialized")
    sd_notify.ready()  # tells systemd (Type=notify) boot genuinely completed, not just forked
    http_server.start()  # blocking


if __name__ == "__main__":
    main()
