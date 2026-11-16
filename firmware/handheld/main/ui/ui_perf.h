/* On-device FPS / render time / CPU overlay. Compiled in with -DHH_UI_PERF=1
 * (PlatformIO: pio run -e esp32s3-perf), absent otherwise.
 *
 * Reads, once a second, bottom right:
 *     25fps 6.1/9.8ms 31kpx c0 12 c1 38
 *     frames drawn | mean / worst render time | pixels redrawn per frame |
 *     busy % of each core (needs FreeRTOS run-time stats, see sdkconfig.perf)
 *
 * Render budget (an ESTIMATE until the overlay has been read on the board).
 * Measured in the simulator with driving data, 20 Hz updates (make budget):
 * the sensor grid redraws most, 32 k px a frame (24 % of the screen); Gauges
 * 20 k, GPS 15 k, Link 8 k. At an assumed 4 M px/s for LVGL's software
 * renderer drawing anti-aliased text into PSRAM on a 240 MHz S3, that is
 * 5 to 8 ms of drawing per frame, plus about 2 ms to copy the same areas into
 * the other frame buffer: roughly 10 ms in every 50, 20 % of core 1. The
 * bounce-buffer interrupt copies the whole frame 50.5 times a second
 * (13 MB/s out of PSRAM), assumed 20 to 30 % of core 1. Core 0 does nothing
 * for the display. A frame has 19.8 ms; a render over ~15 ms slips one.
 */
#ifndef UI_PERF_H
#define UI_PERF_H

#include <stdint.h>
#include "lvgl.h"

#ifndef HH_UI_PERF
#define HH_UI_PERF 0
#endif

typedef struct { uint32_t frames, px, render_us, render_us_max; } ui_perf_totals_t;

void ui_perf_attach(lv_display_t *disp);       /* once, after the display exists */
void ui_perf_flushed(const lv_area_t *area);   /* from the port's flush callback */
void ui_perf_waited(uint32_t us);              /* time the flush spent waiting for the frame swap */
void ui_perf_create(void);                     /* (re)build the overlay label */
void ui_perf_tick(uint32_t now_ms);
/* Running totals since attach; the simulator derives its budget table from them. */
const ui_perf_totals_t *ui_perf_totals(void);

#endif
