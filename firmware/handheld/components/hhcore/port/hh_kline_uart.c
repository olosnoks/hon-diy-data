/* K-line plan B: a UART and an L9637D transceiver (HARDWARE.md).
 *
 * One wire: everything sent comes back on RX, and the link code drops the
 * echo as it does with the cable. The pins are the USB pads (GPIO19/20);
 * routing a UART to them switches the chip's USB pad off (gpio_ll for the
 * S3 does that when the pin function is selected). Not run on hardware. */
#include "driver/uart.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "board_pins.h"
#include "hh_port.h"

#define PORT ((uart_port_t)HH_KLINE_UART_NUM)

static bool installed;

static TickType_t ticks(double s)
{
    if (s <= 0) return 0;
    TickType_t t = (TickType_t)(s * configTICK_RATE_HZ + 0.999);
    return t ? t : 1;
}

static int u_open(void *ctx)
{
    (void)ctx;
    if (installed) return 0;
    const uart_config_t cfg = {
        .baud_rate = 9600, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_DEFAULT,
    };
    if (uart_driver_install(PORT, 512, 0, 0, NULL, 0) != ESP_OK) return -1;
    if (uart_param_config(PORT, &cfg) != ESP_OK
        || uart_set_pin(PORT, HH_KLINE_TX, HH_KLINE_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE) != ESP_OK) {
        uart_driver_delete(PORT);
        return -1;
    }
    /* hand over every byte as it arrives: the link times replies to the byte */
    uart_set_rx_full_threshold(PORT, 1);
    uart_set_rx_timeout(PORT, 1);
    installed = true;
    return 0;
}

static void u_close(void *ctx) { (void)ctx; }      /* a UART does not go away: keep the driver */

static int u_write(void *ctx, const uint8_t *data, size_t len)
{
    (void)ctx;
    return uart_write_bytes(PORT, data, len) == (int)len ? 0 : -1;
}

static int u_read_some(void *ctx, uint8_t *buf, size_t max, double timeout_s)
{
    (void)ctx;
    int n = uart_read_bytes(PORT, buf, 1, ticks(timeout_s));
    if (n <= 0) return n < 0 ? -1 : 0;
    size_t more = 0;
    if (max > 1 && uart_get_buffered_data_len(PORT, &more) == ESP_OK && more) {
        if (more > max - 1) more = max - 1;
        int m = uart_read_bytes(PORT, buf + 1, more, 0);
        if (m > 0) n += m;
    }
    return n;
}

static void u_flush(void *ctx) { (void)ctx; uart_flush_input(PORT); }

static int u_set_baud(void *ctx, uint32_t baud)
{
    (void)ctx;
    uart_wait_tx_done(PORT, pdMS_TO_TICKS(50));
    return uart_set_baudrate(PORT, baud) == ESP_OK ? 0 : -1;
}

static int u_set_break(void *ctx, int on)
{
    (void)ctx;
    /* TX idles high; inverted it sits low, and the L9637D pulls K down */
    return uart_set_line_inverse(PORT, on ? UART_SIGNAL_TXD_INV : UART_SIGNAL_INV_DISABLE) == ESP_OK ? 0 : -1;
}

void hh_kline_uart_init(hh_transport_t *tr)
{
    tr->ctx = NULL;
    tr->open = u_open; tr->close = u_close; tr->write = u_write; tr->read_some = u_read_some;
    tr->flush_input = u_flush; tr->set_baud = u_set_baud; tr->set_break = u_set_break;
    tr->usb_latency_s = 0.0005;         /* the FIFO interrupt, not an adapter */
    tr->backend = HH_BACKEND_UART;
}
