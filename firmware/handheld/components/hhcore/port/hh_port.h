/* ESP-IDF side of hhcore: the two K-line back ends and what they share. */
#ifndef HH_PORT_H_
#define HH_PORT_H_

#include "hhcore.h"

/* Plan A: the S3 as USB host to the KKL cable (FT232R). */
void hh_kline_usb_init(hh_transport_t *tr, int latency_ms);
/* Plan B: UART + L9637D. */
void hh_kline_uart_init(hh_transport_t *tr);

#endif
