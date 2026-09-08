"""Entrypoint. Port of main.c's app_main().

Same init order as the firmware: config -> gpio -> network/wifi -> mqtt ->
modbus tcp -> rs485 -> oled -> apply saved channel config -> serve. (The C
version calls http_server_start() earlier since it's non-blocking there;
here Flask's app.run() is the blocking call, so it's kept last while every
other module still runs in its own background thread, same net effect.)
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
)


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    log = logging.getLogger("main")

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
    http_server.start()  # blocking


if __name__ == "__main__":
    main()
