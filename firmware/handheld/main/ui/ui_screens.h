/* The four screens. Each builds itself into the 480 x 216 body and is told
 * about every snapshot; only one exists at a time.
 */
#ifndef UI_SCREENS_H
#define UI_SCREENS_H

#include <stdbool.h>
#include "lvgl.h"
#include "hh_data.h"

#define UI_SENSORS_PER_PAGE 12     /* 3 columns x 4 rows */

typedef enum { UI_SCR_GAUGES, UI_SCR_SENSORS, UI_SCR_GPS, UI_SCR_LINK, UI_SCR_COUNT } ui_screen_id_t;

/* What the UI works out itself from successive snapshots. */
typedef struct {
    float trip_km;          /* GPS speed integrated while there is a fix */
    uint32_t trip_s;        /* since this drive number was first seen */
    bool acked;             /* the top alarm was acknowledged: no more blinking */
    bool blink_on;          /* 1 Hz phase for unacknowledged alarms */
} ui_derived_t;

int ui_sensor_pages(void);
void ui_screen_title(ui_screen_id_t id, int page, char *buf, size_t n);
void ui_screen_create(ui_screen_id_t id, int page, lv_obj_t *body);
void ui_screen_update(ui_screen_id_t id, const hh_snapshot_t *s, const ui_derived_t *d);

/* Shared with the top bar. */
const char *ui_link_word(uint8_t state);
void ui_link_shape(lv_obj_t *shape, uint8_t state);

#endif
