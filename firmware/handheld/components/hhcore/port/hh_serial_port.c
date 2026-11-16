/* hh_serial on ESP-IDF: the UART behind the USB-C socket (CH340C), the TX
 * task, the RX task (two-way builds), and ESP_LOG lines wrapped as frames.
 * The protocol itself is ../hh_serial.c; SERIAL.md describes it.
 *
 *   default build   UART0, TX GPIO43, RX GPIO44: two-way. UART0 is also the
 *                   ESP-IDF console, so from here on ESP_LOG goes out as LOG
 *                   frames and no plain text is mixed into the stream.
 *   HH_GPS_P1       the GPS owns UART0 and its RX pin at 9600 baud. A UART
 *                   has one baud rate for both directions, so the link uses
 *                   UART1 with only its TX routed to GPIO43: tx-only.
 *
 * The TX task has the lowest priority of the data side and nothing waits for
 * it: producers copy a frame into the ring and go on. If the ring fills, the
 * oldest frames are dropped and counted (hh_serial_push).
 */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"

#include "board_pins.h"
#include "hh_port.h"
#include "hh_serial.h"

#define RING_BYTES      (32 * 1024)     /* about 16 s of the stream; PSRAM */
#define RING_FALLBACK   (8 * 1024)      /* internal RAM, when there is no PSRAM */
#define UART_TX_BUF     4096
#define UART_RX_BUF     2048


/* esp_timer time of the last byte from the laptop (0 = never): the sleep check stays awake while it talks */
volatile int64_t hh_serial_last_rx_us;

static const char *TAG = "hhserial";

static hh_serial_t ser;
static SemaphoreHandle_t ser_mutex;
static uart_port_t ser_port;
static volatile bool running;
static TaskHandle_t tx_handle;
#if !HH_SERIAL_TX_ONLY
static hh_serfs_t serfs;
#endif

static double s_now(void *ctx) { (void)ctx; return (double)esp_timer_get_time() / 1e6; }
static void s_sleep(void *ctx, double s) { (void)ctx; (void)s; }
static void s_lock(void *ctx) { (void)ctx; xSemaphoreTake(ser_mutex, portMAX_DELAY); }
static void s_unlock(void *ctx) { (void)ctx; xSemaphoreGive(ser_mutex); }
static void s_wake(void *ctx) { (void)ctx; if (tx_handle) xTaskNotifyGive(tx_handle); }

/* ESP_LOG's output function: the line becomes a LOG frame. Also returned to
 * nobody: with the binary link on, plain text on this UART would only damage frames. */
static int log_to_frames(const char *fmt, va_list ap)
{
    char line[200];
    int n = vsnprintf(line, sizeof(line), fmt, ap);
    if (n > 0 && running && !xPortInIsrContext() && xTaskGetSchedulerState() == taskSCHEDULER_RUNNING)
        hh_serial_log(&ser, line, (size_t)n < sizeof(line) ? (size_t)n : sizeof(line) - 1);
    return n;
}

/* Each write starts with an extra 0x00. Frames end with one already; the
 * leading one closes anything that is not ours and reached the wire between
 * two writes (a printf in a library, a ROM message), so such text costs the
 * laptop one damaged chunk and never the frame after it. */
static void tx_task(void *arg)
{
    static uint8_t out[1 + 2 * HH_SER_FRAME_MAX];
    (void)arg;
    out[0] = 0;
    for (;;) {
        size_t n = hh_serial_poll(&ser, out + 1, sizeof(out) - 1);
        if (n) uart_write_bytes(ser_port, out, n + 1);  /* waits here, and only here, when the UART is behind */
        else ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(20));  /* until a frame is pushed (or HELLO/STATS fall due) */
    }
}

#if !HH_SERIAL_TX_ONLY
/* One byte with a wait, then whatever else is already in: a command is
 * handled as soon as it is in, not when a buffer fills or a timeout passes. */
static void rx_task(void *arg)
{
    static uint8_t buf[256];
    (void)arg;
    for (;;) {
        int n = uart_read_bytes(ser_port, buf, 1, pdMS_TO_TICKS(100));
        if (n <= 0) { hh_serial_tick(&ser); continue; }     /* quiet: drop an upload that has gone silent */
        size_t more = 0;
        if (uart_get_buffered_data_len(ser_port, &more) == ESP_OK && more) {
            if (more > sizeof(buf) - 1) more = sizeof(buf) - 1;
            int m = uart_read_bytes(ser_port, buf + 1, more, 0);
            if (m > 0) n += m;
        }
        hh_serial_last_rx_us = esp_timer_get_time();
        hh_serial_rx(&ser, buf, (size_t)n);
        hh_serial_tick(&ser);
    }
}
#endif

int hh_serial_port_start(hh_core_t *core, const hh_config_t *cfg, int sd_ok)
{
    if (running || !cfg->serial) return -1;
    const hh_clock_t clk = {NULL, s_now, s_sleep};
    uint32_t size = RING_BYTES;
    uint8_t *mem = heap_caps_malloc(size, MALLOC_CAP_SPIRAM);
    if (!mem) { size = RING_FALLBACK; mem = malloc(size); }
    if (!mem) return -1;
    ser_mutex = xSemaphoreCreateMutex();
    ser_port = (uart_port_t)HH_SERIAL_UART_NUM;

    const uart_config_t ucfg = {
        .baud_rate = cfg->serial_baud, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_DEFAULT,
    };
    if (!uart_is_driver_installed(ser_port)
        && uart_driver_install(ser_port, UART_RX_BUF, UART_TX_BUF, 0, NULL, 0) != ESP_OK) { free(mem); return -1; }
    if (uart_param_config(ser_port, &ucfg) != ESP_OK
        || uart_set_pin(ser_port, HH_SERIAL_TX, HH_SERIAL_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE) != ESP_OK) {
        free(mem);
        return -1;
    }

#if HH_SERIAL_TX_ONLY
    (void)sd_ok;
    hh_serial_init(&ser, &clk, mem, size, HH_SER_VARIANT_GPS_P1, HH_SER_CAP_LOG, (uint32_t)cfg->serial_baud);
#else
    hh_serial_init(&ser, &clk, mem, size, HH_SER_VARIANT_DEFAULT,
                   HH_SER_CAP_RX | HH_SER_CAP_LOG | (sd_ok ? HH_SER_CAP_FILES | HH_SER_CAP_PUT | HH_SER_CAP_MAPUP : 0),
                   (uint32_t)cfg->serial_baud);
    if (sd_ok) hh_serfs_init(&serfs, "/sd", core, &ser.fs);
#endif
    ser.lock = s_lock; ser.unlock = s_unlock; ser.wake = s_wake;
    hh_serial_attach(&ser, core);
    hh_core_set_tap(core, hh_serial_tap, &ser);
    running = true;
    esp_log_set_vprintf(log_to_frames);
#if !HH_SERIAL_TX_ONLY
    /* UART0 is also stdout: a printf now goes through the driver, whole, between two of our writes */
    uart_vfs_dev_use_driver(ser_port);
#endif

    xTaskCreatePinnedToCore(tx_task, "hh_ser_tx", 4096, NULL, 2, &tx_handle, 0);
#if !HH_SERIAL_TX_ONLY
    xTaskCreatePinnedToCore(rx_task, "hh_ser_rx", 8192, NULL, 3, NULL, 0);
#endif
    ESP_LOGI(TAG, "laptop link on UART%d at %d baud, %s", (int)ser_port, (int)cfg->serial_baud,
             HH_SERIAL_TX_ONLY ? "tx-only (GPS on P1)" : "two-way");
    return 0;
}

void hh_serial_port_set_map_hold(int (*hold)(int on)) { ser.map_hold = hold; }

void hh_serial_port_status(uint8_t *mode, uint8_t *host, uint32_t *frames, uint32_t *dropped)
{
    *mode = 0; *host = 0; *frames = 0; *dropped = 0;
    if (!running) return;
    *mode = HH_SERIAL_TX_ONLY ? 1 : 2;
    *host = (uint8_t)hh_serial_host_attached(&ser);
    *frames = ser.tx_frames;
    *dropped = ser.tx_dropped;
}
