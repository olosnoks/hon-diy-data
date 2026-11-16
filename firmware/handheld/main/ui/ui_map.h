/* The Map screen: an offline road map from MAP/ on the SD card, north up,
 * centred on the GPS fix, the drive's trail, stall and marker pins.
 *
 * Tracking (trail, pins, the last position, the automatic zoom) runs on every
 * snapshot whichever screen is up, so the trail is there when the screen is
 * opened. The picture comes from the map worker (ui_mapsvc.h): this file
 * never reads the card and never draws a road.
 */
#ifndef UI_MAP_H
#define UI_MAP_H

#include <stdbool.h>
#include <stdint.h>
#include "lvgl.h"
#include "hh_data.h"

#define UI_MAP_LEVELS 4
#define UI_MAP_HOME_LAT 32.2988          /* Jackson, Mississippi: shown before the first fix ever */
#define UI_MAP_HOME_LON (-90.1848)

void ui_map_reset(void);                /* forget the drive: from ui_create() */
void ui_map_track(const hh_snapshot_t *s, uint32_t now_ms);
void ui_map_create(lv_obj_t *body);
void ui_map_update(const hh_snapshot_t *s, uint32_t now_ms);
void ui_map_zoom_step(void);            /* AUTO -> 1 -> 2 -> 3 -> 4 -> AUTO (the second button) */

/* What the screen is showing, for the simulator's checks. */
typedef struct {
    int level, manual;                  /* manual -1 = automatic */
    int status;                         /* mapsvc_status_t of the picture on screen, 0 = none yet */
    int n_trail, n_pins, n_labels;
    bool banner, message, fix;
    char banner_text[48], message_title[48];
    char labels[12][32];
    uint32_t render_us, renders;
    float mpp;
} ui_map_info_t;
void ui_map_info(ui_map_info_t *out);

#endif
