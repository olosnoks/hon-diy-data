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
#include "driver/uart.h"
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
    const esp_vfs_fat_sdmmc_mount_config_t mount = {.format_if_mount_failed = false, .max_files = 4,
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

static void gps_task(void *arg)
{
    uint8_t buf[128];
    (void)arg;
    for (;;) {
        int n = uart_read_bytes((uart_port_t)HH_GPS_UART_NUM, buf, sizeof(buf), pdMS_TO_TICKS(100));
        if (n > 0) hh_core_gps_bytes(core, buf, (size_t)n);
    }
}

static int gps_start(void)
{
    if (cfg.gps_baud <= 0) return -1;
    const uart_config_t ucfg = {
        .baud_rate = cfg.gps_baud, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE, .source_clk = UART_SCLK_DEFAULT,
    };
    uart_port_t port = (uart_port_t)HH_GPS_UART_NUM;
    if (uart_driver_install(port, 1024, 0, 0, NULL, 0) != ESP_OK) return -1;
    if (uart_param_config(port, &ucfg) != ESP_OK
        || uart_set_pin(port, HH_GPS_TX, HH_GPS_RX, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE) != ESP_OK) return -1;
    return 0;
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
