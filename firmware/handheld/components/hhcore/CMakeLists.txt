# hhcore: the data side of the handheld.
#   *.c       portable C99 (also built for the host: ../../test/host)
#   port/     ESP-IDF glue: tasks, K-line over USB host or UART, SD card, GPS, sleep, the laptop link's UART
idf_component_register(
    SRCS "hh_proto.c" "hh_link.c" "hh_plan.c" "hh_expr.c" "hh_map.c" "hh_alarm.c" "hh_iso.c"
         "hh_nmea.c" "hh_log.c" "hh_text.c" "hh_core.c" "hh_serial.c"
         "port/hh_port.c" "port/hh_kline_usb.c" "port/hh_kline_uart.c" "port/hh_serial_port.c" "port/hh_serfs.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "port" "../../include"
    PRIV_REQUIRES usb usb_host_cdc_acm usb_host_ftdi_vcp esp_driver_uart esp_driver_gpio esp_driver_spi
                  esp_driver_sdspi sdmmc fatfs vfs nvs_flash esp_timer esp_hw_support heap
)
