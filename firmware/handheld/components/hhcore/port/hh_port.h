/* ESP-IDF side of hhcore: the two K-line back ends and what they share. */
#ifndef HH_PORT_H_
#define HH_PORT_H_

#include "hhcore.h"

/* Plan A: the S3 as USB host to the KKL cable (FT232R). */
void hh_kline_usb_init(hh_transport_t *tr, int latency_ms);
/* Plan B: UART + L9637D. */
void hh_kline_uart_init(hh_transport_t *tr);
/* The link to the laptop on the USB-C port (hh_serial_port.c). 0 = started. */
int  hh_serial_port_start(hh_core_t *core, const hh_config_t *cfg, int sd_ok);
/* esp_timer us of the last byte the laptop sent (0 = never) */
extern volatile int64_t hh_serial_last_rx_us;
void hh_serial_port_status(uint8_t *mode, uint8_t *host, uint32_t *frames, uint32_t *dropped);

#endif
