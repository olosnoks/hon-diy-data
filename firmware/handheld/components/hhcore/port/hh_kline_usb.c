/* K-line plan A: the ESP32-S3 as USB host to the owner's KKL cable (FT232R,
 * 0403:6001), through espressif/usb_host_ftdi_vcp 2.1.1 on
 * usb_host_cdc_acm 2.4.1 (vendored in ../../).
 *
 * Wiring (HARDWARE.md "Plan A"): the cable's USB plug cut off, D- on GPIO19
 * and D+ on GPIO20 (header P3, silkscreen "USB"), its +5 V and GND on header
 * P1. The S3's own full-speed PHY does the USB; nothing here touches GPIO19/20
 * as GPIO (that is plan B, hh_kline_uart.c, only with backend = uart).
 *
 * VBUS: the board cannot switch it; the cable is powered from P1 5 V all the
 * time. The host library forces VBUS-valid in host mode and powers its root
 * port by itself at install, so nothing waits on a VBUS switch here.
 *
 * What the driver does: open, baud rate, 8N1, receive with FTDI's two status
 * bytes per packet stripped. What it does not do, and is done here with the
 * FTDI vendor requests Linux's ftdi_sio uses, via
 * cdc_acm_host_send_custom_request():
 *   latency timer   0x40, 0x09, wValue = ms       (default 16 ms: too slow)
 *   break on/off    0x40, 0x04, wValue = 0x0008 | 0x4000 when on  (8N1 + break bit)
 * Break is only needed for the ISO 9141-2 5-baud init, 200 ms per bit.
 * DTR and RTS are raised after open, as pyserial and Web Serial leave them on
 * the laptop (the same cable works there that way).
 *
 * Baud: the FT232R makes 10400 as 3 MHz / 288.5 = 10398.6 (-0.01 %) and 9600
 * exactly (312.5); the driver picks those fractional divisors.
 *
 * No cable: every open fails after its connection timeout and the engine
 * (hh_core.c PORT_LOST) tries again every 0.25-2 s, forever; link state 4
 * "K-line cable not found". A pulled cable ends the open handle (DISCONNECTED
 * event -> every call returns -1 -> PORT_LOST -> close -> reopen): plugging it
 * back in recovers without a reboot. If no USB device ever enumerates, a
 * warning names the likely wiring faults (see enum_diag()).
 *
 * Compiled against the real headers; never run against a cable. See the
 * "not verified" list in HARDWARE.md and bring-up steps 9 to 13 there. */
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "sdkconfig.h"
#include "usb/cdc_acm_host.h"
#include "usb/cdc_acm_host_ops.h"
#include "usb/usb_host.h"
#include "usb/vcp_ftdi.h"

#include "board_pins.h"
#include "hh_port.h"

/* The console must not be on the S3's USB: GPIO19/20 are this host port. sdkconfig.defaults keeps it on UART0
 * (the CH340C on the USB-C) or, in the GPS-on-P1 build, off. */
#if defined(CONFIG_ESP_CONSOLE_USB_CDC) || defined(CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG)
#error "console on the S3's own USB: GPIO19/20 are the USB host port for the KKL cable; use UART0 or none"
#endif

#define FTDI_OUT_REQ          0x40      /* vendor, host to device */
#define FTDI_REQ_SET_DATA     0x04
#define FTDI_REQ_SET_LATENCY  0x09
#define FTDI_DATA_8N1         0x0008
#define FTDI_DATA_BREAK       0x4000
#define FTDI_PORT_INDEX       0         /* single-port chip, as ftdi_sio addresses it */

#define OPEN_TIMEOUT_MS       500       /* per product id tried; the engine retries with its own growing pause */
#define DIAG_FIRST_S          10        /* no cable for this long: say why it may be */
#define DIAG_EVERY_S          60        /* and again this often */

static const char *TAG = "kline-usb";

static cdc_acm_dev_hdl_t dev;
static StreamBufferHandle_t rx;
static volatile bool gone;              /* the cable was pulled (set in the driver's task) */
static bool lib_installed, lib_task_up, cdc_installed;
static int latency_ms = 1;
static hh_transport_t *self;

/* enumeration diagnostics (new_dev() runs in the CDC driver's task) */
static volatile uint32_t devs_seen, ftdi_seen;
static volatile uint16_t other_vid, other_pid;
static int64_t missing_since_us, next_diag_us;

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

/* Any device that finished enumerating on the port: proof that D+/D-, 5 V and GND are right. */
static void new_dev(usb_device_handle_t usb_dev)
{
    const usb_device_desc_t *d = NULL;
    devs_seen++;
    if (usb_host_get_device_descriptor(usb_dev, &d) != ESP_OK || !d) return;
    if (d->idVendor == VCP_FTDI_VID && (d->idProduct == VCP_FTDI_PID_FT232 || d->idProduct == VCP_FTDI_PID_FT231)) {
        ftdi_seen++;
        ESP_LOGI(TAG, "USB device %04x:%04x on GPIO19/20: the FTDI KKL cable", d->idVendor, d->idProduct);
    } else {
        other_vid = d->idVendor;
        other_pid = d->idProduct;
        ESP_LOGW(TAG, "USB device %04x:%04x on GPIO19/20: not an FTDI FT232R/FT231X (0403:6001 / 6015), not used",
                 d->idVendor, d->idProduct);
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

/* Each step once; a step that failed is tried again on the next open. */
static int host_start(void)
{
    esp_err_t err;
    if (!rx) rx = xStreamBufferCreate(1024, 1);
    if (!rx) return -1;
    if (!lib_installed) {
#if defined(CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG) && CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG
        ESP_LOGW(TAG, "this build keeps a secondary console on USB-Serial-JTAG (a stale sdkconfig): "
                      "delete sdkconfig.esp32s3 and rebuild so sdkconfig.defaults turns it off");
#endif
        /* the internal PHY, OTG controller, host mode: GPIO19 D-, GPIO20 D+ (fixed pads). The library also
           powers the root port; VBUS itself comes from P1 5 V and is not switched. */
        const usb_host_config_t host_cfg = {.skip_phy_setup = false, .intr_flags = ESP_INTR_FLAG_LEVEL1};
        err = usb_host_install(&host_cfg);
        if (err != ESP_OK) { ESP_LOGE(TAG, "usb_host_install: %s", esp_err_to_name(err)); return -1; }
        lib_installed = true;
        ESP_LOGI(TAG, "USB host up on GPIO%d (D-) / GPIO%d (D+), waiting for the KKL cable", HH_USBH_DM, HH_USBH_DP);
    }
    if (!lib_task_up) {
        if (xTaskCreatePinnedToCore(usb_lib_task, "usb_lib", 4096, NULL, 10, NULL, 0) != pdPASS) return -1;
        lib_task_up = true;
    }
    if (!cdc_installed) {
        const cdc_acm_host_driver_config_t drv = {
            .driver_task_stack_size = 4096, .driver_task_priority = 10, .xCoreID = 0, .new_dev_cb = new_dev,
        };
        err = cdc_acm_host_install(&drv);
        if (err != ESP_OK) { ESP_LOGE(TAG, "cdc_acm_host_install: %s", esp_err_to_name(err)); return -1; }
        cdc_installed = true;
    }
    return 0;
}

/* The cable did not open. After DIAG_FIRST_S without it (then every DIAG_EVERY_S) say what the host saw. */
static void enum_diag(esp_err_t err)
{
    int64_t t = esp_timer_get_time();
    if (!missing_since_us) { missing_since_us = t; next_diag_us = t + DIAG_FIRST_S * 1000000LL; }
    if (t < next_diag_us) return;
    next_diag_us = t + DIAG_EVERY_S * 1000000LL;
    int s = (int)((t - missing_since_us) / 1000000);
    usb_host_lib_info_t info = {0};
    usb_host_lib_info(&info);
    if (devs_seen == 0 && info.num_devices == 0) {
        ESP_LOGW(TAG, "K-line cable not found: no USB device has connected on GPIO19/20 in %d s. Check: cable D- "
                      "on P3 IO19 and D+ on P3 IO20 (swapped D+/D- looks just like this), the cable's +5 V on P1 "
                      "pin 1 and GND on P1 pin 4, and whether R3/R4 (4.7k pull-ups on GPIO19/20, HARDWARE.md) "
                      "are fitted: they hold both lines high and the host never sees a device connect. Retrying.", s);
    } else if (!ftdi_seen && devs_seen) {
        ESP_LOGW(TAG, "K-line cable not found in %d s: a USB device enumerated (%04x:%04x), but not the FTDI "
                      "KKL cable 0403:6001", s, other_vid, other_pid);
    } else {
        ESP_LOGW(TAG, "K-line cable not found in %d s: %d USB device(s) enumerated (FTDI seen %u times) but the "
                      "cable did not open: %s", s, info.num_devices, (unsigned)ftdi_seen, esp_err_to_name(err));
    }
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
        .connection_timeout_ms = OPEN_TIMEOUT_MS,
        .out_buffer_size = 64,
        .in_buffer_size = 0,                /* the FTDI driver wants the endpoint's own packet size */
        .event_cb = on_event,
        .data_cb = on_rx,
        .user_arg = NULL,
    };
    gone = false;
    esp_err_t err = ftdi_vcp_open(FTDI_PID_AUTO, 0, &cfg, &dev);    /* tries 0403:6001 first, then 6015 */
    if (err != ESP_OK) { dev = NULL; enum_diag(err); return -1; }
    if (set_line(9600) != 0) { ESP_LOGW(TAG, "the cable refused 9600 8N1"); k_close(NULL); return -1; }
    /* A clone chip may refuse the latency request. The link then still works,
     * at the default 16 ms, and the timing code is told so. */
    if (cdc_acm_host_send_custom_request(dev, FTDI_OUT_REQ, FTDI_REQ_SET_LATENCY, (uint16_t)latency_ms,
                                         FTDI_PORT_INDEX, 0, NULL) == ESP_OK) {
        self->usb_latency_s = latency_ms / 1000.0;
    } else {
        ESP_LOGW(TAG, "the cable refused the latency timer request: staying at 16 ms");
        self->usb_latency_s = 0.016;
    }
    /* DTR and RTS on, as on the laptop. Not needed by most KKL cables; harmless if refused. */
    if (cdc_acm_host_set_control_line_state(dev, true, true) != ESP_OK)
        ESP_LOGW(TAG, "the cable refused DTR/RTS: carrying on");
    while (xStreamBufferReceive(rx, scratch, sizeof(scratch), 0) > 0) {}
    if (missing_since_us)
        ESP_LOGI(TAG, "KKL cable found after %d s", (int)((esp_timer_get_time() - missing_since_us) / 1000000));
    missing_since_us = 0;
    ESP_LOGI(TAG, "KKL cable open, 8N1 (the engine sets the map's baud), latency %d ms", (int)(self->usb_latency_s * 1000));
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
    if (!rx) return;
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
