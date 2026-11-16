/* hondalog handheld UI. Portable: the same code runs on the board
 * (main/port/port_esp32.c) and in the PC simulator (sim/).
 *
 * Everything here is called from the one LVGL thread.
 *
 * One-button UX (BOOT, GPIO0):
 *   short press        next screen (sensor pages count as screens)
 *   hold 1 s, release  MARK
 *   hold 3 s           acknowledge the alarm if one is blinking, else Day/Night
 *   any press          dismisses the stall-captured screen
 * An optional second button steps back a screen; on the Map screen a short
 * press of it steps the zoom (AUTO, 1..4) and a 1 s hold steps back.
 */
#ifndef HH_UI_API_H
#define HH_UI_API_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define UI_DATA_PERIOD_MS 50       /* snapshot -> widgets, 20 Hz */

/* Seconds without a press before the sensor grid turns its own page; 0 = never. */
#ifndef HH_UI_AUTOCYCLE_S
#define HH_UI_AUTOCYCLE_S 0
#endif

void ui_create(bool night);
/* Call every 10 ms or so with the debounce-free button levels. */
void ui_tick(uint32_t now_ms, bool button, bool button_back);
bool ui_night(void);

/* For the simulator's screenshots. */
void ui_goto(int stop);            /* 0 .. ui_stops() - 1 */
int ui_stops(void);

/* Provided by the port. */
void ui_port_theme_changed(bool night);   /* set the backlight, remember the choice */
int64_t ui_port_micros(void);
/* Busy percentage of each core over the last call interval, -1 = unknown. */
void ui_port_cpu(int *core0, int *core1);

/* The Map screen (ui_map.c, ui_mapsvc.c). The board runs the map worker in a
 * low-priority task on core 0 (port_map_esp32.c); the simulator runs it in place. */
void ui_port_map_kick(void);                    /* there is work for mapsvc_work() */
void ui_port_map_lock(void);                    /* guards the hand-off; never held for long */
void ui_port_map_unlock(void);
void *ui_port_map_alloc(size_t n);              /* canvases and the tile cache: PSRAM on the board */
void ui_port_map_free(void *p);
const char *ui_port_map_dir(void);              /* the MAP folder: "/sd/MAP" */
bool ui_port_last_pos(double *lat, double *lon);    /* the last fix kept over power-off (NVS) */
void ui_port_save_pos(double lat, double lon);

#endif
