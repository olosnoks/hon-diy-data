/* hhcore on ESP-IDF: the public API of hh_data.h, the tasks, the SD card,
 * the GPS UART, the clock and sleep.
 *
 *   link task      hh_core_step(): owns the K-line (USB host cable or UART)
 *   gps task       NMEA bytes in
 *   storage task   writes what the engine queued; free space; the sleep decision
 *   serial tasks   the link to the laptop (hh_serial_port.c), below all of these
 *
 * All on core 0; the display has core 1. One mutex guards what the tasks and
 * the UI share; it is never held across a K-line exchange or a card write.
 *
 * Drive files are written through FatFs directly, not stdio: f_expand takes
 * a file's space in one contiguous piece when it is created, so logging
 * writes data sectors only (FORMAT.md, "What makes it safe at key-off").
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "diskio_impl.h"
#include "diskio_sdmmc.h"
#include "driver/gpio.h"
#include "driver/sdspi_host.h"
#include "driver/spi_common.h"
#include "driver/pulse_cnt.h"
#include "driver/uart.h"
#include "esp_private/gpio.h"
#include "esp_rom_gpio.h"
#include "soc/io_mux_reg.h"
#include "soc/uart_periph.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_rom_sys.h"
#include "esp_sleep.h"
#include "esp_timer.h"
#include "esp_vfs_fat.h"
#include "ff.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "sdmmc_cmd.h"

#include "board_pins.h"
#include "hh_port.h"

#ifndef HH_SD_SPI_HOST
#define HH_SD_SPI_HOST SPI2_HOST
#endif
#ifndef HH_GPS_BAUD
#define HH_GPS_BAUD 9600
#endif

#define MOUNT          "/sd"
#define MAP_PATH       MOUNT "/HANDHELD.MAP"
#define CFG_PATH       MOUNT "/HANDHELD.CFG"
#define QUIET_PROBE_S  5.0      /* woken by the timer: how long to ask the ECU before sleeping again */

static const char *TAG = "hhcore";

static const uint8_t BUILTIN_MAP[] = {
#include "hh_default_map.inc"
};

static hh_core_t *core;
static hh_map_t map;
static hh_config_t cfg;
static hh_transport_t transport;
static SemaphoreHandle_t mutex;
static sdmmc_card_t *card;
static char fat_drive[4] = "0:";
static char device_id[12];
static bool started;

/* ---- clock, lock ---------------------------------------------------------- */

static double clk_now(void *ctx) { (void)ctx; return (double)esp_timer_get_time() / 1e6; }

static void clk_sleep(void *ctx, double s)
{
    (void)ctx;
    if (s <= 0) return;
    if (s < 0.001) { esp_rom_delay_us((uint32_t)(s * 1e6)); return; }
    vTaskDelay((TickType_t)(s * configTICK_RATE_HZ + 0.5));
}

static void lock_take(void *ctx) { (void)ctx; xSemaphoreTake(mutex, portMAX_DELAY); }
static void lock_give(void *ctx) { (void)ctx; xSemaphoreGive(mutex); }

/* The ESP32 has no battery clock: the time is only right after a GPS fix
 * (and stays right through deep sleep). */
static int64_t time_get(void *ctx)
{
    struct timeval tv;
    (void)ctx;
    gettimeofday(&tv, NULL);
    return tv.tv_sec > 1600000000 ? (int64_t)tv.tv_sec * 1000000 + tv.tv_usec : 0;
}

static void time_set(void *ctx, int64_t unix_us)
{
    struct timeval tv = {.tv_sec = (time_t)(unix_us / 1000000), .tv_usec = (suseconds_t)(unix_us % 1000000)};
    (void)ctx;
    settimeofday(&tv, NULL);
    ESP_LOGI(TAG, "clock set from GPS");
}

/* ---- SD card ---------------------------------------------------------------- */

static void *io_open(void *ctx, const char *path, const char *mode)
{
    char full[48];
    (void)ctx; (void)mode;
    if (!card) return NULL;
    FIL *f = malloc(sizeof(FIL));
    if (!f) return NULL;
    snprintf(full, sizeof(full), "%s%s", fat_drive, path);
    if (f_open(f, full, FA_WRITE | FA_CREATE_ALWAYS) != FR_OK) { free(f); return NULL; }
    return f;
}

static int io_write(void *ctx, void *fh, const void *data, size_t len)
{
    UINT done = 0;
    (void)ctx;
    return f_write(fh, data, (UINT)len, &done) == FR_OK && done == len ? 0 : -1;
}

static int io_sync(void *ctx, void *fh) { (void)ctx; return f_sync(fh) == FR_OK ? 0 : -1; }

static int io_close(void *ctx, void *fh)
{
    (void)ctx;
    FRESULT r = f_close(fh);
    free(fh);
    return r == FR_OK ? 0 : -1;
}

static int io_preallocate(void *ctx, void *fh, uint32_t len)
{
    (void)ctx;
    /* one contiguous piece, allocated now; the file pointer stays at 0 */
    if (f_expand(fh, len, 1) != FR_OK) return -1;
    return f_sync(fh) == FR_OK ? 0 : -1;
}

static int io_truncate(void *ctx, void *fh, uint32_t len)
{
    (void)ctx;
    if (f_lseek(fh, len) != FR_OK) return -1;
    return f_truncate(fh) == FR_OK ? 0 : -1;
}

static int sd_mount(void)
{
    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = HH_SD_SPI_HOST;
    host.max_freq_khz = 20000;
    const spi_bus_config_t bus = {.mosi_io_num = HH_SD_MOSI, .miso_io_num = HH_SD_MISO, .sclk_io_num = HH_SD_SCK,
                                  .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = 8192};
    if (spi_bus_initialize(host.slot, &bus, SDSPI_DEFAULT_DMA) != ESP_OK) return -1;
    sdspi_device_config_t slot = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot.gpio_cs = HH_SD_CS;
    slot.host_id = host.slot;
    /* max_files: stdio files open at once; the Map screen keeps MAP/MAP.IDX and MAP/MAP.DAT open */
    const esp_vfs_fat_sdmmc_mount_config_t mount = {.format_if_mount_failed = false, .max_files = 6,
                                                    .allocation_unit_size = 32 * 1024};
    if (esp_vfs_fat_sdspi_mount(MOUNT, &host, &slot, &mount, &card) != ESP_OK) {
        card = NULL;
        spi_bus_free(host.slot);
        return -1;
    }
    BYTE pdrv = ff_diskio_get_pdrv_card(card);
    if (pdrv != 0xFF) { fat_drive[0] = (char)('0' + pdrv); fat_drive[1] = ':'; fat_drive[2] = 0; }
    mkdir(MOUNT "/DRIVES", 0777);
    mkdir(MOUNT "/CAPTURES", 0777);
    return 0;
}

static float sd_free_mb(void)
{
    uint64_t total = 0, free_bytes = 0;
    if (!card || esp_vfs_fat_info(MOUNT, &total, &free_bytes) != ESP_OK) return 0;
    return (float)(free_bytes / (1024.0 * 1024.0));
}

/* Highest nnnnnn among <letter>nnnnnn.HHD in a folder. */
static uint32_t last_number(const char *dir, char letter)
{
    char path[32];
    FF_DIR d;
    FILINFO info;
    uint32_t best = 0;
    snprintf(path, sizeof(path), "%s/%s", fat_drive, dir);
    if (!card || f_opendir(&d, path) != FR_OK) return 0;
    while (f_readdir(&d, &info) == FR_OK && info.fname[0]) {
        if ((info.fname[0] | 0x20) != (letter | 0x20)) continue;
        uint32_t n = (uint32_t)strtoul(info.fname + 1, NULL, 10);
        if (n > best) best = n;
    }
    f_closedir(&d);
    return best;
}

static void load_config(void)
{
    static char text[2048];
    hh_config_default(&cfg);
    FILE *f = card ? fopen(CFG_PATH, "rb") : NULL;
    if (!f) return;
    size_t n = fread(text, 1, sizeof(text), f);
    fclose(f);
    int bad = hh_config_parse(&cfg, text, n);
    if (bad) ESP_LOGW(TAG, "HANDHELD.CFG: %d line(s) skipped", bad);
}

/* 1 = the card's map is in use, 0 = the built-in one. */
static int load_map(void)
{
    hh_map_hdr_t hdr;
    FILE *f = card ? fopen(MAP_PATH, "rb") : NULL;
    if (f) {
        uint8_t *buf = NULL;
        int rc = -1;
        if (fread(&hdr, 1, sizeof(hdr), f) == sizeof(hdr) && hdr.magic == HH_MAP_MAGIC
            && hdr.core_len >= sizeof(hdr) && hdr.core_len <= HH_MAP_MAX_CORE
            && (buf = malloc(hdr.core_len)) != NULL) {
            memcpy(buf, &hdr, sizeof(hdr));
            if (fread(buf + sizeof(hdr), 1, hdr.core_len - sizeof(hdr), f) == hdr.core_len - sizeof(hdr))
                rc = hh_map_parse(&map, buf, hdr.core_len);
        }
        fclose(f);
        if (rc == 0) return 1;          /* buf stays: the map points into it */
        free(buf);
        ESP_LOGW(TAG, "HANDHELD.MAP refused (%d): using the built-in map", rc);
    }
    if (hh_map_parse(&map, BUILTIN_MAP, sizeof(BUILTIN_MAP)) != 0) {
        ESP_LOGE(TAG, "the built-in map does not load");
        abort();
    }
    return 0;
}

static uint32_t next_boot_no(void)
{
    nvs_handle_t h;
    uint32_t n = 0;
    esp_err_t err = nvs_flash_init();           /* fine if the application already did */
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    if (nvs_open("hhcore", NVS_READWRITE, &h) != ESP_OK) return (uint32_t)esp_timer_get_time() | 0x80000000u;
    nvs_get_u32(h, "boot", &n);
    n++;
    nvs_set_u32(h, "boot", n);
    nvs_commit(h);
    nvs_close(h);
    return n;
}

/* ---- tasks -------------------------------------------------------------------- */

static void link_task(void *arg)
{
    (void)arg;
    for (;;) hh_core_step(core);        /* every step ends in an exchange or a wait */
}

/* GPS diagnostics, logged every 2 s (as log frames on the laptop link).
 * Hardware pulse counters watch the data pin and the PPS pin, independent of
 * the UART: falling edges 0 means nothing ever drives that line low. If the
 * NMEA shows up on the PPS pin instead (P3 wired the other way round), the two
 * are swapped at run time and the UART moves to the busy pin. PPS rising edges
 * are time-stamped (esp_timer us) for the clock. */
#ifndef HH_GPS_PPS
#define HH_GPS_PPS (-1)
#endif
enum { PIN_DATA, PIN_PPS, N_PROBE };
static int gps_pin[N_PROBE] = {HH_GPS_RX, HH_GPS_PPS};
static int gps_status = -99;            /* 0 running, else the step that failed */
static uint32_t gps_bytes;
static char gps_tail[49];
static pcnt_unit_handle_t gps_pcnt[N_PROBE];
static volatile int gps_overflows[N_PROBE];
static volatile int64_t pps_last_us;
static volatile uint32_t pps_count;

static bool gps_pcnt_overflow(pcnt_unit_handle_t u, const pcnt_watch_event_data_t *e, void *ctx)
{
    (void)e; (void)ctx;
    for (int i = 0; i < N_PROBE; i++)       /* by handle: the roles can swap */
        if (gps_pcnt[i] == u) gps_overflows[i]++;
    return false;
}

static void IRAM_ATTR pps_isr(void *arg)
{
    (void)arg;
    pps_last_us = esp_timer_get_time();
    pps_count++;
}

static void probe_init(int i)
{
    if (gps_pin[i] < 0) return;
    pcnt_unit_config_t uc = {.low_limit = -1, .high_limit = 30000};
    if (pcnt_new_unit(&uc, &gps_pcnt[i]) != ESP_OK) { gps_pcnt[i] = NULL; return; }
    pcnt_chan_config_t cc = {.edge_gpio_num = gps_pin[i], .level_gpio_num = -1};
    pcnt_channel_handle_t ch;
    if (pcnt_new_channel(gps_pcnt[i], &cc, &ch) != ESP_OK
        || pcnt_channel_set_edge_action(ch, PCNT_CHANNEL_EDGE_ACTION_HOLD, PCNT_CHANNEL_EDGE_ACTION_INCREASE) != ESP_OK) {
        pcnt_del_unit(gps_pcnt[i]); gps_pcnt[i] = NULL; return;
    }
    pcnt_unit_add_watch_point(gps_pcnt[i], 30000);
    pcnt_event_callbacks_t cbs = {.on_reach = gps_pcnt_overflow};
    pcnt_unit_register_event_callbacks(gps_pcnt[i], &cbs, NULL);
    pcnt_unit_enable(gps_pcnt[i]);
    pcnt_unit_clear_count(gps_pcnt[i]);
    pcnt_unit_start(gps_pcnt[i]);
}

static void pps_attach(int pin)
{
    static int attached = -1;
    if (attached >= 0) gpio_isr_handler_remove((gpio_num_t)attached);
    attached = -1;
    if (pin < 0) return;
    esp_err_t e = gpio_install_isr_service(0);
    if (e != ESP_OK && e != ESP_ERR_INVALID_STATE) return;
    gpio_set_intr_type((gpio_num_t)pin, GPIO_INTR_POSEDGE);
    if (gpio_isr_handler_add((gpio_num_t)pin, pps_isr, NULL) == ESP_OK) {
        gpio_intr_enable((gpio_num_t)pin);
        attached = pin;
    }
}

/* Route the GPS RX through the GPIO matrix, never the IOMUX shortcut, so the pulse counter on the same pin keeps working. */
static void gps_route_rx(int pin)
{
    uart_set_pin((uart_port_t)HH_GPS_UART_NUM, HH_GPS_TX, pin, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    gpio_func_sel((gpio_num_t)pin, PIN_FUNC_GPIO);
    gpio_set_direction((gpio_num_t)pin, GPIO_MODE_INPUT); /* gpio_input_enable() does not exist in IDF 5.3.1 */
    esp_rom_gpio_connect_in_signal(pin, UART_PERIPH_SIGNAL(HH_GPS_UART_NUM, SOC_UART_RX_PIN_IDX), false);
}

static void gps_diag_init(void)
{
    for (int i = 0; i < N_PROBE; i++) probe_init(i);
    pps_attach(gps_pin[PIN_PPS]);
}

static uint32_t probe_edges(int i)
{
    int c = 0;
    if (!gps_pcnt[i] || pcnt_unit_get_count(gps_pcnt[i], &c) != ESP_OK) return 0;
    return (uint32_t)gps_overflows[i] * 30000u + (uint32_t)c;
}

static void gps_diag_log(void)
{
    static uint32_t last_bytes, last_edges[N_PROBE], last_pps;
    uint32_t edges[N_PROBE], d[N_PROBE];
    for (int i = 0; i < N_PROBE; i++) { edges[i] = probe_edges(i); d[i] = edges[i] - last_edges[i]; }

    /* data on the PPS pin and nothing on the data pin: P3 is the other way round */
    if (gps_status == 0 && gps_pin[PIN_PPS] >= 0 && d[PIN_PPS] > 200 && d[PIN_PPS] > 20 * (d[PIN_DATA] + 1)) {
        int t = gps_pin[PIN_DATA]; gps_pin[PIN_DATA] = gps_pin[PIN_PPS]; gps_pin[PIN_PPS] = t;
        pcnt_unit_handle_t u = gps_pcnt[PIN_DATA]; gps_pcnt[PIN_DATA] = gps_pcnt[PIN_PPS]; gps_pcnt[PIN_PPS] = u;
        int o = gps_overflows[PIN_DATA]; gps_overflows[PIN_DATA] = gps_overflows[PIN_PPS]; gps_overflows[PIN_PPS] = o;
        uint32_t e = edges[PIN_DATA]; edges[PIN_DATA] = edges[PIN_PPS]; edges[PIN_PPS] = e;
        e = d[PIN_DATA]; d[PIN_DATA] = d[PIN_PPS]; d[PIN_PPS] = e;
        e = last_edges[PIN_DATA]; last_edges[PIN_DATA] = last_edges[PIN_PPS]; last_edges[PIN_PPS] = e;
        gps_route_rx(gps_pin[PIN_DATA]);
        pps_attach(gps_pin[PIN_PPS]);
        ESP_LOGW(TAG, "gps: NMEA found on GPIO%d, PPS expected on GPIO%d: swapped (P3 wired the other way round)",
                 gps_pin[PIN_DATA], gps_pin[PIN_PPS]);
    }

    hh_snapshot_t s;
    hh_snapshot(&s);
    char tail[sizeof(gps_tail)];
    memcpy(tail, gps_tail, sizeof(tail));
    uint32_t pps = pps_count;
    int64_t age = pps_last_us ? (esp_timer_get_time() - pps_last_us) / 1000 : -1;
    ESP_LOGI(TAG, "gps diag: reader %s, uart%d data GPIO%d @%d baud (%s), bytes %u (+%u), data edges %u (+%u), "
             "PPS GPIO%d edges +%u pulses %u (+%u, last %lld ms ago), sentences ok %u bad %u, fix %u sats %u, last \"%s\"",
             gps_status == 0 ? "running" : "NOT running", (int)HH_GPS_UART_NUM, gps_pin[PIN_DATA], (int)cfg.gps_baud,
             gps_pin[PIN_DATA] >= 0 ? (gpio_get_level((gpio_num_t)gps_pin[PIN_DATA]) ? "high" : "LOW") : "-",
             (unsigned)gps_bytes, (unsigned)(gps_bytes - last_bytes), (unsigned)edges[PIN_DATA], (unsigned)d[PIN_DATA],
             gps_pin[PIN_PPS], (unsigned)d[PIN_PPS], (unsigned)pps, (unsigned)(pps - last_pps), (long long)age,
             (unsigned)s.nmea.good, (unsigned)s.nmea.bad, (unsigned)s.gps.fix, (unsigned)s.gps.sats, tail);
    last_bytes = gps_bytes;
    for (int i = 0; i < N_PROBE; i++) last_edges[i] = edges[i];
    last_pps = pps;
}

static void gps_task(void *arg)
{
    uint8_t buf[128];
    (void)arg;
    int64_t next_diag = esp_timer_get_time() + 2000000;
    for (;;) {
        int n = uart_read_bytes((uart_port_t)HH_GPS_UART_NUM, buf, sizeof(buf), pdMS_TO_TICKS(100));
        if (n > 0) {
            hh_core_gps_bytes(core, buf, (size_t)n);
            gps_bytes += (uint32_t)n;
            /* keep the last 48 bytes as printable text for the log */
            size_t keep = sizeof(gps_tail) - 1, len = strlen(gps_tail);
            for (int i = 0; i < n; i++) {
                char ch = (buf[i] >= 32 && buf[i] < 127) ? (char)buf[i] : (buf[i] == '\n' ? '|' : '.');
                if (len == keep) { memmove(gps_tail, gps_tail + 1, keep - 1); len--; }
                gps_tail[len++] = ch;
                gps_tail[len] = 0;
            }
        }
        if (esp_timer_get_time() >= next_diag) { gps_diag_log(); next_diag += 2000000; }
    }
}

/* Without a running reader the diagnostics still need to be logged */
static void gps_diag_task(void *arg)
{
    (void)arg;
    for (;;) { vTaskDelay(pdMS_TO_TICKS(2000)); gps_diag_log(); }
}

static int gps_start(void)
{
    if (cfg.gps_baud <= 0) { ESP_LOGW(TAG, "gps: off (gps_baud = 0)"); return gps_status = -4; }
    const uart_config_t ucfg = {
        .baud_rate = cfg.gps_baud, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_DEFAULT,
    };
    uart_port_t port = (uart_port_t)HH_GPS_UART_NUM;
    esp_err_t e;
    if ((e = uart_driver_install(port, 1024, 0, 0, NULL, 0)) != ESP_OK) {
        ESP_LOGE(TAG, "gps: uart%d driver install failed: %s", (int)port, esp_err_to_name(e));
        return gps_status = -1;
    }
    if ((e = uart_param_config(port, &ucfg)) != ESP_OK) {
        ESP_LOGE(TAG, "gps: uart%d config failed: %s", (int)port, esp_err_to_name(e));
        return gps_status = -2;
    }
    if ((e = uart_set_pin(port, HH_GPS_TX, HH_GPS_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE)) != ESP_OK) {
        ESP_LOGE(TAG, "gps: uart%d set pin rx %d failed: %s", (int)port, (int)HH_GPS_RX, esp_err_to_name(e));
        return gps_status = -3;
    }
    gps_diag_init();
    gps_route_rx(gps_pin[PIN_DATA]);
    ESP_LOGI(TAG, "gps: uart%d rx pin %d at %d baud", (int)port, (int)HH_GPS_RX, (int)cfg.gps_baud);
    return gps_status = 0;
}

__attribute__((weak)) void hh_ui_before_sleep(void) {}

/* The ECU has been silent for long enough: close the files and sleep. On a
 * supply that stays on with the key off (OBD2 pin 16) this is what keeps the
 * battery alive. Wakes on the timer, to ask the ECU, and on the BOOT button. */
static void go_to_sleep(void)
{
    ESP_LOGI(TAG, "ECU silent: sleeping, probe every %d s", (int)cfg.sleep_probe_s);
    if (core) {
        hh_core_stop(core, HH_END_SLEEP);
        while (hh_core_storage_step(core)) {}
    }
    if (card) esp_vfs_fat_sdcard_unmount(MOUNT, card);
    hh_ui_before_sleep();
    esp_sleep_enable_timer_wakeup((uint64_t)cfg.sleep_probe_s * 1000000ULL);
    esp_sleep_enable_ext0_wakeup((gpio_num_t)HH_BTN_BOOT, 0);
    esp_deep_sleep_start();
}

static void storage_task(void *arg)
{
    int64_t next_check = 0;
    (void)arg;
    for (;;) {
        if (!hh_core_storage_step(core)) vTaskDelay(pdMS_TO_TICKS(20));
        int64_t t = esp_timer_get_time();
        if (t < next_check) continue;
        next_check = t + 2000000;
        hh_core_set_storage(core, card != NULL, sd_free_mb());
        if (cfg.sleep_after_s > 0
            && hh_core_idle_s(core) >= (double)cfg.sleep_after_s + (double)cfg.drive_gap_s) go_to_sleep();
    }
}

/* Woken by the sleep timer: is the key on? Ask before the display comes up. */
static void quiet_probe(void)
{
    hh_snapshot_t s;
    double until = clk_now(NULL) + QUIET_PROBE_S;
    while (clk_now(NULL) < until) {
        hh_core_step(core);
        hh_core_snapshot(core, &s);
        if (s.link.state == HH_LINK_OK || s.link.state == HH_LINK_ISO_ONLY) return;
    }
    go_to_sleep();
}

/* ---- hh_data.h -------------------------------------------------------------------- */

int hh_display_rotation(void) { return cfg.rotate == 180 ? 180 : 0; }

void hh_core_start(void)
{
    static hh_platform_t plat;
    uint8_t mac[6] = {0};
    if (started) return;
    started = true;
    mutex = xSemaphoreCreateMutex();

    if (sd_mount() != 0) ESP_LOGW(TAG, "no SD card: live data only");
    load_config();
    int card_map = load_map();
    esp_efuse_mac_get_default(mac);
    snprintf(device_id, sizeof(device_id), "HH-%02X%02X%02X", mac[3], mac[4], mac[5]);

    if (cfg.backend == HH_CFG_BACKEND_UART) hh_kline_uart_init(&transport);
    else hh_kline_usb_init(&transport, (int)cfg.usb_latency_ms);

    plat.clk.now = clk_now; plat.clk.sleep = clk_sleep;
    plat.transport = &transport;
    plat.io.open = io_open; plat.io.write = io_write; plat.io.sync = io_sync; plat.io.close = io_close;
    plat.io.preallocate = io_preallocate; plat.io.truncate = io_truncate;
    plat.lock = lock_take; plat.unlock = lock_give;
    plat.set_time = time_set; plat.get_time = time_get;
    plat.device = device_id;
    plat.boot_no = next_boot_no();
    plat.last_drive_no = last_number("DRIVES", 'D');
    plat.last_capture_no = last_number("CAPTURES", 'C');
    plat.nonce_seed = (uint32_t)esp_timer_get_time() ^ ((uint32_t)mac[4] << 8 | mac[5]) ^ (plat.boot_no << 16);
    plat.builtin_map = !card_map;
    /* 30 s of reads is about 60 KB; the queue holds a capture's worth plus slack for a slow card */
    plat.ring_size = 256 * 1024;
    plat.ring_mem = heap_caps_malloc(plat.ring_size, MALLOC_CAP_SPIRAM);
    plat.queue_size = 256 * 1024;
    plat.queue_mem = heap_caps_malloc(plat.queue_size, MALLOC_CAP_SPIRAM);
    if (!plat.ring_mem || !plat.queue_mem) {        /* no PSRAM: a short pre-trigger, still a log */
        ESP_LOGW(TAG, "no PSRAM for the rings: using 16 KB + 24 KB of internal RAM");
        heap_caps_free(plat.ring_mem); heap_caps_free(plat.queue_mem);
        plat.ring_size = 16 * 1024; plat.ring_mem = malloc(plat.ring_size);
        plat.queue_size = 24 * 1024; plat.queue_mem = malloc(plat.queue_size);
    }
    void *mem = heap_caps_malloc(hh_core_size(), MALLOC_CAP_SPIRAM);
    if (!mem) mem = malloc(hh_core_size());
    core = hh_core_init(mem, &map, &cfg, &plat);
    hh_core_set_storage(core, card != NULL, sd_free_mb());
    ESP_LOGI(TAG, "map #%.12s (%s), %u channels, K-line on %s, boot %u", map.hdr.map_hash,
             card_map ? "card" : "built in", (unsigned)map.n_ch,
             transport.backend == HH_BACKEND_USB ? "USB host" : "UART", (unsigned)plat.boot_no);

    if (esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER && cfg.sleep_after_s > 0) quiet_probe();

    /* from here on ESP_LOG lines are frames on the laptop link, not text on the console */
    if (cfg.serial && hh_serial_port_start(core, &cfg, card != NULL) != 0)
        ESP_LOGW(TAG, "the laptop link did not start");

    xTaskCreatePinnedToCore(link_task, "hh_link", 8192, NULL, 6, NULL, 0);
    xTaskCreatePinnedToCore(storage_task, "hh_store", 6144, NULL, 4, NULL, 0);
    if (gps_start() == 0) xTaskCreatePinnedToCore(gps_task, "hh_gps", 4096, NULL, 5, NULL, 0);
    else xTaskCreatePinnedToCore(gps_diag_task, "hh_gpsdiag", 3072, NULL, 2, NULL, 0);
}

const hh_channel_info_t *hh_channels(uint16_t *n)
{
    if (!core) { if (n) *n = 0; return NULL; }
    return hh_core_channels(core, n);
}

void hh_snapshot(hh_snapshot_t *out)
{
    if (!core) {
        memset(out, 0, sizeof(*out));
        out->top_alarm_ch = -1;
        return;
    }
    hh_core_snapshot(core, out);
    hh_serial_port_status(&out->pc.mode, &out->pc.host, &out->pc.frames, &out->pc.dropped);
}

void hh_mark(void) { if (core) hh_core_mark(core); }
void hh_mark_at(int64_t t_us) { if (core) hh_core_mark_at(core, t_us); }
void hh_ack_alarm(void) { if (core) hh_core_ack(core); }
void hh_request_dtc(void) { if (core) hh_core_request_dtc(core); }

int16_t hh_channel_index(const char *name)
{
    uint16_t n = 0;
    const hh_channel_info_t *ch = hh_channels(&n);
    for (uint16_t i = 0; i < n; i++) if (strcmp(ch[i].name, name) == 0) return (int16_t)i;
    return -1;
}
