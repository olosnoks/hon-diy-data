/* HAL for the ESP32-S3 (ESP-IDF 5.1 or newer). See bb_hal.h for what each
 * function must do and HARDWARE.md for the wiring behind the pin table. */
#include <dirent.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <unistd.h>

#include "driver/gpio.h"
#include "driver/mcpwm_cap.h"
#include "driver/sdspi_host.h"
#include "driver/spi_common.h"
#include "driver/uart.h"
#include "driver/usb_serial_jtag.h"
#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_continuous.h"
#include "esp_heap_caps.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "esp_vfs_fat.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "sdmmc_cmd.h"

#include "bb_hal.h"

/* ---- pins (ESP32-S3-DevKitC-1; GPIO 35-37 belong to the octal PSRAM,
 *      19/20 are USB, 0/3/45/46 are strapping pins: all left alone) ------- */
#define PIN_ADC_VBAT   4        /* ADC1_CH3 */
#define PIN_ADC_ECUB   5        /* ADC1_CH4 */
#define PIN_ADC_PUMP   6        /* ADC1_CH5 */
#define PIN_ADC_IG1    7        /* ADC1_CH6 */
#define PIN_ADC_ALT    8        /* ADC1_CH7, optional */
#define PIN_GPS_RX     9
#define PIN_SD_CS      10
#define PIN_SD_MOSI    11
#define PIN_SD_SCK     12
#define PIN_SD_MISO    13
#define PIN_WAKE       14       /* IG1 through its own divider: wakes the box */
#define PIN_CAP_TACH   15
#define PIN_CAP_IGN    16
#define PIN_CAP_INJ    17
#define PIN_BUTTON     18       /* to ground, internal pull-up */
#define PIN_LED        21

#define MOUNT "/sd"
#define EDGE_Q 1024             /* power of two; 7,000 rpm is about 1,200 edges/s in all */

#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 2, 0)
#define BB_ATTEN ADC_ATTEN_DB_12
#else
#define BB_ATTEN ADC_ATTEN_DB_11
#endif

static const char *TAG = "bb";
static const uint8_t ADC_CHAN[BB_N_ANALOG] = {ADC_CHANNEL_3, ADC_CHANNEL_4, ADC_CHANNEL_5,
                                              ADC_CHANNEL_6, ADC_CHANNEL_7};
static const int CAP_PIN[BB_N_EDGE] = {PIN_CAP_TACH, PIN_CAP_IGN, PIN_CAP_INJ};

static SemaphoreHandle_t mutex;
static char device_id[12];

/* ---- clock, memory, identity ------------------------------------------- */

void hal_init(void)
{
    mutex = xSemaphoreCreateMutex();
    gpio_config_t out = {.pin_bit_mask = 1ULL << PIN_LED, .mode = GPIO_MODE_OUTPUT};
    gpio_config(&out);
    gpio_config_t in = {.pin_bit_mask = 1ULL << PIN_BUTTON, .mode = GPIO_MODE_INPUT,
                        .pull_up_en = GPIO_PULLUP_ENABLE};
    gpio_config(&in);
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    uint8_t mac[6] = {0};
    esp_efuse_mac_get_default(mac);
    snprintf(device_id, sizeof(device_id), "S3-%02X%02X%02X", mac[3], mac[4], mac[5]);
}

uint64_t hal_now_us(void) { return (uint64_t)esp_timer_get_time(); }

void *hal_big_alloc(size_t size)
{
    void *p = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    return p ? p : heap_caps_malloc(size, MALLOC_CAP_8BIT);
}

size_t hal_big_free(void)
{
    size_t ext = heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    size_t in = heap_caps_get_largest_free_block(MALLOC_CAP_8BIT);
    return ext > in ? ext : in / 2;             /* without PSRAM leave half the heap alone */
}

const char *hal_device_id(void) { return device_id; }

uint32_t hal_next_boot_no(void)
{
    nvs_handle_t h;
    uint32_t n = 0;
    if (nvs_open("blackbox", NVS_READWRITE, &h) != ESP_OK) return 0;
    nvs_get_u32(h, "boot", &n);
    n++;
    nvs_set_u32(h, "boot", n);
    nvs_commit(h);
    nvs_close(h);
    return n;
}

/* ---- ADC: continuous DMA, calibrated through a lookup table ------------- */

static adc_continuous_handle_t adc;
static uint16_t *adc_lut;                       /* raw 0..4095 -> pin mV */
static uint32_t adc_total_hz, adc_overrun_count;
static volatile uint64_t adc_frame_us;          /* when the newest DMA frame completed */

static bool adc_done_cb(adc_continuous_handle_t h, const adc_continuous_evt_data_t *ev, void *ctx)
{
    (void)h; (void)ev; (void)ctx;
    adc_frame_us = (uint64_t)esp_timer_get_time();
    return false;
}

static bool adc_ovf_cb(adc_continuous_handle_t h, const adc_continuous_evt_data_t *ev, void *ctx)
{
    (void)h; (void)ev; (void)ctx;
    adc_overrun_count++;
    return false;
}

int hal_adc_start(uint32_t per_channel_hz)
{
    adc_total_hz = per_channel_hz * BB_N_ANALOG;
    const uint32_t frame = BB_N_ANALOG * 25 * SOC_ADC_DIGI_RESULT_BYTES;      /* 5 ms */
    adc_continuous_handle_cfg_t hcfg = {.max_store_buf_size = frame * 16, .conv_frame_size = frame};
    if (adc_continuous_new_handle(&hcfg, &adc) != ESP_OK) return -1;

    adc_digi_pattern_config_t pattern[BB_N_ANALOG];
    for (int i = 0; i < BB_N_ANALOG; i++) {
        pattern[i].atten = BB_ATTEN;
        pattern[i].channel = ADC_CHAN[i];
        pattern[i].unit = ADC_UNIT_1;
        pattern[i].bit_width = SOC_ADC_DIGI_MAX_BITWIDTH;
    }
    adc_continuous_config_t cfg = {
        .pattern_num = BB_N_ANALOG, .adc_pattern = pattern, .sample_freq_hz = adc_total_hz,
        .conv_mode = ADC_CONV_SINGLE_UNIT_1, .format = ADC_DIGI_OUTPUT_FORMAT_TYPE2,
    };
    if (adc_continuous_config(adc, &cfg) != ESP_OK) return -1;

    /* Factory calibration (eFuse) through the curve-fitting scheme, expanded
     * once into a table so the 25,000 conversions a second cost a lookup. */
    adc_lut = heap_caps_malloc(4096 * sizeof(uint16_t), MALLOC_CAP_8BIT);
    if (!adc_lut) return -1;
    adc_cali_handle_t cali = NULL;
    adc_cali_curve_fitting_config_t ccfg = {.unit_id = ADC_UNIT_1, .atten = BB_ATTEN,
                                            .bitwidth = ADC_BITWIDTH_12};
    int have_cali = adc_cali_create_scheme_curve_fitting(&ccfg, &cali) == ESP_OK;
    if (!have_cali) ESP_LOGW(TAG, "no ADC calibration in eFuse; using the nominal 3100 mV full scale");
    for (int raw = 0; raw < 4096; raw++) {
        int mv = raw * 3100 / 4095;
        if (have_cali) adc_cali_raw_to_voltage(cali, raw, &mv);
        adc_lut[raw] = (uint16_t)(mv < 0 ? 0 : mv);
    }
    if (have_cali) adc_cali_delete_scheme_curve_fitting(cali);

    adc_continuous_evt_cbs_t cbs = {.on_conv_done = adc_done_cb, .on_pool_ovf = adc_ovf_cb};
    adc_continuous_register_event_callbacks(adc, &cbs, NULL);
    return adc_continuous_start(adc) == ESP_OK ? 0 : -1;
}

uint32_t hal_adc_conv_hz(void) { return adc_total_hz; }
uint32_t hal_adc_overruns(void) { return adc_overrun_count; }

int hal_adc_read(bb_conv_t *conv, size_t max, uint64_t *t_us, uint32_t timeout_ms)
{
    static uint8_t raw[BB_N_ANALOG * 25 * SOC_ADC_DIGI_RESULT_BYTES];
    uint32_t got = 0, want = (uint32_t)max * SOC_ADC_DIGI_RESULT_BYTES;
    if (want > sizeof(raw)) want = sizeof(raw);
    if (adc_continuous_read(adc, raw, want, &got, timeout_ms) != ESP_OK) return 0;
    *t_us = adc_frame_us ? adc_frame_us : (uint64_t)esp_timer_get_time();
    int n = 0;
    for (uint32_t i = 0; i + SOC_ADC_DIGI_RESULT_BYTES <= got; i += SOC_ADC_DIGI_RESULT_BYTES) {
        const adc_digi_output_data_t *d = (const adc_digi_output_data_t *)&raw[i];
        if (d->type2.unit != 0) continue;
        for (int c = 0; c < BB_N_ANALOG; c++) {
            if (ADC_CHAN[c] != d->type2.channel) continue;
            conv[n].ch = (uint8_t)c;
            conv[n].mv = adc_lut[d->type2.data & 0xFFF];
            n++;
            break;
        }
    }
    return n;
}

/* ---- edges: MCPWM capture, counter latched in hardware on each edge ----- */

static hal_edge_t edge_q[EDGE_Q];
static volatile uint32_t edge_head, edge_tail, edge_drop;
static uint32_t cap_hz = 80000000;
static uint64_t cap_epoch_us;                   /* esp_timer time of capture count cap_epoch */
static uint32_t cap_epoch;
static uint8_t cap_have_epoch;

static bool cap_cb(mcpwm_cap_channel_handle_t chan, const mcpwm_capture_event_data_t *ev, void *ctx)
{
    (void)chan;
    const uint64_t now = (uint64_t)esp_timer_get_time();
    if (!cap_have_epoch) { cap_epoch = ev->cap_value; cap_epoch_us = now; cap_have_epoch = 1; }
    /* The 32-bit count wraps every 54 s at 80 MHz. Both clocks run off the
     * same crystal, so "now" says which lap we are in and the latched count
     * says exactly where: the timestamp does not include interrupt latency. */
    const uint32_t per_us = cap_hz / 1000000u;
    uint64_t hint = (now - cap_epoch_us) * per_us;
    uint64_t ticks = bb_extend32(hint, ev->cap_value - cap_epoch);
    uint32_t head = edge_head;
    if (head - edge_tail >= EDGE_Q) { edge_drop++; return false; }
    hal_edge_t *e = &edge_q[head & (EDGE_Q - 1)];
    e->t_us = cap_epoch_us + ticks / per_us;
    e->ch = (uint8_t)(uintptr_t)ctx;
    e->gpio_level = ev->cap_edge == MCPWM_CAP_EDGE_POS;
    edge_head = head + 1;
    return false;
}

int hal_edges_start(void)
{
    mcpwm_cap_timer_handle_t timer = NULL;
    mcpwm_capture_timer_config_t tcfg = {.group_id = 0, .clk_src = MCPWM_CAPTURE_CLK_SRC_DEFAULT};
    if (mcpwm_new_capture_timer(&tcfg, &timer) != ESP_OK) return -1;
    mcpwm_capture_timer_get_resolution(timer, &cap_hz);
    for (int ch = 0; ch < BB_N_EDGE; ch++) {
        mcpwm_cap_channel_handle_t chan = NULL;
        mcpwm_capture_channel_config_t ccfg = {
            .gpio_num = CAP_PIN[ch], .prescale = 1,
            .flags.pos_edge = true, .flags.neg_edge = true, .flags.pull_up = true,
        };
        if (mcpwm_new_capture_channel(timer, &ccfg, &chan) != ESP_OK) return -1;
        mcpwm_capture_event_callbacks_t cbs = {.on_cap = cap_cb};
        mcpwm_capture_channel_register_event_callbacks(chan, &cbs, (void *)(uintptr_t)ch);
        mcpwm_capture_channel_enable(chan);
    }
    mcpwm_capture_timer_enable(timer);
    return mcpwm_capture_timer_start(timer) == ESP_OK ? 0 : -1;
}

size_t hal_edges_read(hal_edge_t *out, size_t max)
{
    size_t n = 0;
    uint32_t tail = edge_tail;                  /* single reader: the acq task */
    while (n < max && tail != edge_head) out[n++] = edge_q[tail++ & (EDGE_Q - 1)];
    edge_tail = tail;
    /* three interrupt sources can queue slightly out of order: sort by time */
    for (size_t i = 1; i < n; i++) {
        hal_edge_t e = out[i];
        size_t j = i;
        while (j > 0 && out[j - 1].t_us > e.t_us) { out[j] = out[j - 1]; j--; }
        out[j] = e;
    }
    return n;
}

uint32_t hal_edges_dropped(void) { return edge_drop; }
uint32_t hal_edges_resolution_hz(void) { return cap_hz; }

int  hal_button(void) { return gpio_get_level(PIN_BUTTON) == 0; }
void hal_led(int on) { gpio_set_level(PIN_LED, on ? 1 : 0); }

/* ---- storage: microSD over SPI, FAT ------------------------------------- */

static sdmmc_card_t *card;

static void full_path(char *out, size_t cap, const char *path) { snprintf(out, cap, MOUNT "%s", path); }

static void *io_open(void *ctx, const char *path, const char *mode)
{
    char full[64];
    (void)ctx;
    full_path(full, sizeof(full), path);
    FILE *f = fopen(full, mode);
    if (f) setvbuf(f, NULL, _IOFBF, 8192);      /* whole blocks reach the card in one go */
    return f;
}
static int io_write(void *ctx, void *fh, const void *data, size_t len)
{
    (void)ctx;
    return fwrite(data, 1, len, fh) == len ? 0 : -1;
}
static int io_read(void *ctx, void *fh, void *data, size_t len) { (void)ctx; return (int)fread(data, 1, len, fh); }
static int io_seek(void *ctx, void *fh, uint32_t off) { (void)ctx; return fseek(fh, (long)off, SEEK_SET); }
static long io_size(void *ctx, void *fh)
{
    struct stat st;
    (void)ctx;
    fflush(fh);
    return fstat(fileno(fh), &st) == 0 ? (long)st.st_size : -1;
}
static int io_sync(void *ctx, void *fh)
{
    (void)ctx;
    if (fflush(fh) != 0) return -1;
    return fsync(fileno(fh));                   /* FAT directory entry and size hit the card */
}
static int io_close(void *ctx, void *fh) { (void)ctx; return fclose(fh); }

int hal_storage_mount(bb_io_t *io)
{
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.max_freq_khz = 20000;
    spi_bus_config_t bus = {.mosi_io_num = PIN_SD_MOSI, .miso_io_num = PIN_SD_MISO,
                            .sclk_io_num = PIN_SD_SCK, .quadwp_io_num = -1, .quadhd_io_num = -1,
                            .max_transfer_sz = 8192};
    if (spi_bus_initialize(host.slot, &bus, SDSPI_DEFAULT_DMA) != ESP_OK) return -1;
    sdspi_device_config_t dev = SDSPI_DEVICE_CONFIG_DEFAULT();
    dev.gpio_cs = PIN_SD_CS;
    dev.host_id = host.slot;
    esp_vfs_fat_sdmmc_mount_config_t mcfg = {.format_if_mount_failed = false, .max_files = 6,
                                             .allocation_unit_size = 32 * 1024};
    if (esp_vfs_fat_sdspi_mount(MOUNT, &host, &dev, &mcfg, &card) != ESP_OK) { card = NULL; return -1; }
    io->ctx = NULL; io->open = io_open; io->write = io_write; io->read = io_read;
    io->seek = io_seek; io->size = io_size; io->sync = io_sync; io->close = io_close;
    return 0;
}

void hal_storage_unmount(void)
{
    if (card) esp_vfs_fat_sdcard_unmount(MOUNT, card);
    card = NULL;
}

int hal_storage_mkdir(const char *path)
{
    char full[64];
    full_path(full, sizeof(full), path);
    return mkdir(full, 0775);
}

uint32_t hal_storage_last_event_no(void)
{
    uint32_t best = 0;
    DIR *d = opendir(MOUNT "/EVENTS");
    if (!d) return 0;
    for (struct dirent *e; (e = readdir(d)) != NULL; ) {
        unsigned long no;
        char ext[4] = "";
        if (sscanf(e->d_name, "EV%6lu.%3s", &no, ext) == 2 && strcasecmp(ext, "BBX") == 0 && no > best)
            best = (uint32_t)no;
    }
    closedir(d);
    return best;
}

int hal_storage_read_text(const char *path, char *out, size_t cap)
{
    char full[64];
    full_path(full, sizeof(full), path);
    FILE *f = fopen(full, "rb");
    if (!f) return -1;
    size_t n = fread(out, 1, cap - 1, f);
    fclose(f);
    out[n] = 0;
    return (int)n;
}

int hal_storage_write_text(const char *path, const char *text, size_t len)
{
    char full[64];
    full_path(full, sizeof(full), path);
    FILE *f = fopen(full, "wb");
    if (!f) return -1;
    int ok = fwrite(text, 1, len, f) == len;
    fclose(f);
    return ok ? 0 : -1;
}

int hal_storage_selftest(void)
{
    static const char probe[] = "hondalog black box self-test 0123456789";
    char back[sizeof(probe)] = "";
    if (!card) return -1;
    if (hal_storage_write_text("/SELFTEST.TMP", probe, sizeof(probe) - 1) != 0) return -1;
    int n = hal_storage_read_text("/SELFTEST.TMP", back, sizeof(back));
    unlink(MOUNT "/SELFTEST.TMP");
    return n == (int)sizeof(probe) - 1 && memcmp(probe, back, sizeof(probe) - 1) == 0 ? 0 : -1;
}

/* ---- USB: the chip's built-in USB Serial/JTAG (CDC-ACM, 303a:1001) ------ */

int hal_usb_start(void)
{
    usb_serial_jtag_driver_config_t cfg = {.tx_buffer_size = 8192, .rx_buffer_size = 2048};
    return usb_serial_jtag_driver_install(&cfg) == ESP_OK ? 0 : -1;
}

int hal_usb_read(uint8_t *buf, size_t max, uint32_t timeout_ms)
{
    return usb_serial_jtag_read_bytes(buf, (uint32_t)max, pdMS_TO_TICKS(timeout_ms));
}

void hal_usb_write(const uint8_t *data, size_t len)
{
    /* zero wait: with no laptop listening the buffer fills and frames are
     * dropped. A torn frame fails its CRC on the other side. */
    usb_serial_jtag_write_bytes(data, len, 0);
}

/* ---- GPS ---------------------------------------------------------------- */

int hal_gps_start(uint32_t baud)
{
    uart_config_t cfg = {.baud_rate = (int)baud, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
                         .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE};
    if (uart_driver_install(UART_NUM_1, 1024, 0, 0, NULL, 0) != ESP_OK) return -1;
    uart_param_config(UART_NUM_1, &cfg);
    return uart_set_pin(UART_NUM_1, UART_PIN_NO_CHANGE, PIN_GPS_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE) == ESP_OK ? 0 : -1;
}

int hal_gps_read(uint8_t *buf, size_t max) { return uart_read_bytes(UART_NUM_1, buf, (uint32_t)max, 0); }

/* ---- tasks, lock, sleep, log -------------------------------------------- */

void hal_task_start(void (*fn)(void *), const char *name, int priority, size_t stack, int core)
{
    xTaskCreatePinnedToCore(fn, name, (uint32_t)stack, NULL, (UBaseType_t)(tskIDLE_PRIORITY + 4 + priority),
                            NULL, core);
}

void hal_sleep_ms(uint32_t ms) { vTaskDelay(pdMS_TO_TICKS(ms) ? pdMS_TO_TICKS(ms) : 1); }
void hal_lock(void *ctx) { (void)ctx; xSemaphoreTake(mutex, portMAX_DELAY); }
void hal_unlock(void *ctx) { (void)ctx; xSemaphoreGive(mutex); }

void hal_deep_sleep(void)
{
    /* wake when IG1 comes up (PIN_WAKE high). The button is on a pull-up to a
     * rail that is off in deep sleep, so it cannot wake the chip. */
    esp_sleep_enable_ext1_wakeup(1ULL << PIN_WAKE, ESP_EXT1_WAKEUP_ANY_HIGH);
    esp_deep_sleep_start();
}

void hal_log(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    esp_log_writev(ESP_LOG_INFO, TAG, fmt, ap);     /* UART0, never the USB data port */
    va_end(ap);
}

void bb_app_main(void);
void app_main(void) { bb_app_main(); }
