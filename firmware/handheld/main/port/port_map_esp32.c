/* Board side of the Map screen: the map worker's task, its memory, and the
 * last position kept over power-off.
 *
 *   map task   core 0, priority 1: below hh-core's link (6), GPS (5), storage
 *              (4) and serial (2-3) tasks, so a map redraw or a tile read
 *              never delays the K-line, a GPS sentence or a log write. It
 *              never runs on core 1, where the UI and the display live.
 *              It blocks on a semaphore until the UI posts a view or takes a
 *              picture; with nothing to draw it warms the tile cache.
 *   memory     the two 480 x 216 canvases (2 x 207 KB) and the tile cache
 *              (1.5 MB) in PSRAM.
 *   card       stdio through ESP-IDF's FAT VFS at /sd (hh-core mounts it).
 *              FatFs serialises the volume, so a tile read waits at most for
 *              one log write and the reverse.
 */
#include <stdbool.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "nvs.h"

#include "ui.h"
#include "ui_mapsvc.h"

#define MAP_TASK_CORE   0
#define MAP_TASK_PRIO   1
#define MAP_TASK_STACK  (6 * 1024)

static const char *TAG = "map";
static SemaphoreHandle_t lock, kick;
static TaskHandle_t task;

static void map_task(void *arg)
{
    (void)arg;
    for (;;) {
        if (mapsvc_work()) {
            vTaskDelay(1);              /* more to do: let IDLE0 run (task watchdog), then go on */
            continue;
        }
        xSemaphoreTake(kick, pdMS_TO_TICKS(1000));
    }
}

static void start(void)
{
    if (task) return;
    lock = xSemaphoreCreateMutex();
    kick = xSemaphoreCreateBinary();
    if (xTaskCreatePinnedToCore(map_task, "map", MAP_TASK_STACK, NULL, MAP_TASK_PRIO, &task, MAP_TASK_CORE) != pdPASS) {
        ESP_LOGE(TAG, "no map task");
        task = NULL;
    }
}

void ui_port_map_kick(void)
{
    start();
    if (kick) xSemaphoreGive(kick);
}

void ui_port_map_lock(void)
{
    start();
    xSemaphoreTake(lock, portMAX_DELAY);
}

void ui_port_map_unlock(void) { xSemaphoreGive(lock); }

void *ui_port_map_alloc(size_t n)
{
    void *p = heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!p) p = heap_caps_malloc(n, MALLOC_CAP_8BIT);
    return p;
}

void ui_port_map_free(void *p) { heap_caps_free(p); }

const char *ui_port_map_dir(void) { return "/sd/MAP"; }

/* ---- the last fix in NVS ------------------------------------------------------- */

bool ui_port_last_pos(double *lat, double *lon)
{
    nvs_handle_t h;
    double v[2];
    size_t n = sizeof(v);
    bool ok = false;
    if (nvs_open("hhui", NVS_READONLY, &h) == ESP_OK) {
        ok = nvs_get_blob(h, "lastpos", v, &n) == ESP_OK && n == sizeof(v) &&
             v[0] >= -90 && v[0] <= 90 && v[1] >= -180 && v[1] <= 180;
        nvs_close(h);
    }
    if (ok) { *lat = v[0]; *lon = v[1]; }
    return ok;
}

void ui_port_save_pos(double lat, double lon)
{
    nvs_handle_t h;
    double v[2] = {lat, lon};
    if (nvs_open("hhui", NVS_READWRITE, &h) == ESP_OK) {
        nvs_set_blob(h, "lastpos", v, sizeof(v));
        nvs_commit(h);
        nvs_close(h);
    }
}
