/* K-line plan A: the ESP32-S3 as USB host to the owner's KKL cable (FT232R,
 * 0403:6001), through espressif/usb_host_ftdi_vcp 2.1.1 on
 * usb_host_cdc_acm 2.4.1 (vendored in ../../).
 *
 * What the driver does: open, baud rate, 8N1, receive with FTDI's two status
 * bytes per packet stripped. What it does not do, and is done here with the
 * FTDI vendor requests Linux's ftdi_sio uses, via
 * cdc_acm_host_send_custom_request():
 *   latency timer   0x40, 0x09, wValue = ms       (default 16 ms: too slow)
 *   break on/off    0x40, 0x04, wValue = 0x0008 | 0x4000 when on  (8N1 + break bit)
 * Break is only needed for the ISO 9141-2 5-baud init, 200 ms per bit.
 *
 * Compiled against the real headers; never run against a cable. See the
 * "not verified" list in the final notes of HARDWARE.md and bring-up steps
 * 9 to 13 there. */
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"

#include "esp_log.h"
#include "usb/cdc_acm_host.h"
#include "usb/cdc_acm_host_ops.h"
#include "usb/usb_host.h"
#include "usb/vcp_ftdi.h"

#include "board_pins.h"
#include "hh_port.h"

#define FTDI_OUT_REQ          0x40      /* vendor, host to device */
#define FTDI_REQ_SET_DATA     0x04
#define FTDI_REQ_SET_LATENCY  0x09
#define FTDI_DATA_8N1         0x0008
#define FTDI_DATA_BREAK       0x4000
#define FTDI_PORT_INDEX       0         /* single-port chip, as ftdi_sio addresses it */

static const char *TAG = "kline-usb";

static cdc_acm_dev_hdl_t dev;
static StreamBufferHandle_t rx;
static volatile bool gone;              /* the cable was pulled (set in the driver's task) */
static bool host_up;
static int latency_ms = 1;
static hh_transport_t *self;

static TickType_t ticks(double s)
{
    if (s <= 0) return 0;
    TickType_t t = (TickType_t)(s * configTICK_RATE_HZ + 0.999);
    return t ? t : 1;
}

static void usb_lib_task(void *arg)
{
    (void)arg;
    for (;;) {
        uint32_t flags = 0;
        usb_host_lib_handle_events(portMAX_DELAY, &flags);
        if (flags & USB_HOST_LIB_EVENT_FLAGS_NO_CLIENTS) usb_host_device_free_all();
    }
}

/* Driver task. The FTDI driver has already removed the two status bytes. */
static bool on_rx(const uint8_t *data, size_t len, void *arg)
{
    (void)arg;
    if (len) xStreamBufferSend(rx, data, len, 0);
    return true;
}

static void on_event(const cdc_acm_host_dev_event_data_t *event, void *arg)
{
    (void)arg;
    if (event->type == CDC_ACM_HOST_DEVICE_DISCONNECTED || event->type == CDC_ACM_HOST_ERROR) gone = true;
}

static int host_start(void)
{
    if (host_up) return 0;
    const usb_host_config_t host_cfg = {.skip_phy_setup = false, .intr_flags = ESP_INTR_FLAG_LEVEL1};
    if (!rx) rx = xStreamBufferCreate(1024, 1);
    if (!rx) return -1;
    esp_err_t err = usb_host_install(&host_cfg);
    if (err != ESP_OK) { ESP_LOGE(TAG, "usb_host_install: %s", esp_err_to_name(err)); return -1; }
    if (xTaskCreatePinnedToCore(usb_lib_task, "usb_lib", 4096, NULL, 10, NULL, 0) != pdPASS) return -1;
    err = cdc_acm_host_install(NULL);
    if (err != ESP_OK) { ESP_LOGE(TAG, "cdc_acm_host_install: %s", esp_err_to_name(err)); return -1; }
    host_up = true;
    return 0;
}

static int set_line(uint32_t baud)
{
    const cdc_acm_line_coding_t coding = {.dwDTERate = baud, .bCharFormat = 0, .bParityType = 0, .bDataBits = 8};
    return cdc_acm_host_line_coding_set(dev, &coding) == ESP_OK ? 0 : -1;
}

static void k_close(void *ctx)
{
    (void)ctx;
    if (dev) {
        cdc_acm_host_close(dev);
        dev = NULL;
    }
}

static int k_open(void *ctx)
{
    uint8_t scratch[64];
    (void)ctx;
    if (host_start() != 0) return -1;
    k_close(NULL);
    const cdc_acm_host_device_config_t cfg = {
        .connection_timeout_ms = 500,       /* the engine retries with its own growing pause */
        .out_buffer_size = 64,
        .in_buffer_size = 0,                /* the FTDI driver wants the endpoint's own packet size */
        .event_cb = on_event,
        .data_cb = on_rx,
        .user_arg = NULL,
    };
    if (ftdi_vcp_open(FTDI_PID_AUTO, 0, &cfg, &dev) != ESP_OK) { dev = NULL; return -1; }
    gone = false;
    if (set_line(9600) != 0) { k_close(NULL); return -1; }
    /* A clone chip may refuse the latency request. The link then still works,
     * at the default 16 ms, and the timing code is told so. */
    if (cdc_acm_host_send_custom_request(dev, FTDI_OUT_REQ, FTDI_REQ_SET_LATENCY, (uint16_t)latency_ms,
                                         FTDI_PORT_INDEX, 0, NULL) == ESP_OK) {
        self->usb_latency_s = latency_ms / 1000.0;
    } else {
        ESP_LOGW(TAG, "the cable refused the latency timer request: staying at 16 ms");
        self->usb_latency_s = 0.016;
    }
    while (xStreamBufferReceive(rx, scratch, sizeof(scratch), 0) > 0) {}
    ESP_LOGI(TAG, "KKL cable open, 9600 8N1, latency %d ms", (int)(self->usb_latency_s * 1000));
    return 0;
}

static int k_write(void *ctx, const uint8_t *data, size_t len)
{
    (void)ctx;
    if (!dev || gone) return -1;
    return cdc_acm_host_data_tx_blocking(dev, data, len, 200) == ESP_OK ? 0 : -1;
}

static int k_read_some(void *ctx, uint8_t *buf, size_t max, double timeout_s)
{
    (void)ctx;
    if (!dev || gone) return -1;
    size_t n = xStreamBufferReceive(rx, buf, max, ticks(timeout_s));
    if (!n && gone) return -1;
    return (int)n;
}

static void k_flush(void *ctx)
{
    uint8_t scratch[64];
    (void)ctx;
    while (xStreamBufferReceive(rx, scratch, sizeof(scratch), 0) > 0) {}
}

static int k_set_baud(void *ctx, uint32_t baud)
{
    (void)ctx;
    if (!dev || gone) return -1;
    return set_line(baud);          /* this also ends a break: the break bit shares the request */
}

static int k_set_break(void *ctx, int on)
{
    (void)ctx;
    if (!dev || gone) return -1;
    uint16_t value = FTDI_DATA_8N1 | (on ? FTDI_DATA_BREAK : 0);
    return cdc_acm_host_send_custom_request(dev, FTDI_OUT_REQ, FTDI_REQ_SET_DATA, value, FTDI_PORT_INDEX, 0,
                                            NULL) == ESP_OK ? 0 : -1;
}

void hh_kline_usb_init(hh_transport_t *tr, int latency)
{
    self = tr;
    latency_ms = latency < 1 ? 1 : latency > 255 ? 255 : latency;
    tr->ctx = NULL;
    tr->open = k_open; tr->close = k_close; tr->write = k_write; tr->read_some = k_read_some;
    tr->flush_input = k_flush; tr->set_baud = k_set_baud; tr->set_break = k_set_break;
    tr->usb_latency_s = latency_ms / 1000.0;
    tr->backend = HH_BACKEND_USB;
}
