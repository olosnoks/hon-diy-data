/* The channel tile, in two sizes: BIG for the Gauges screen (154 x 103, 52 px
 * digits, a linear gauge and the verified marker beside it) and SMALL for the sensor grid (154 x 50, 36 px
 * digits). States follow HANDOFF.md section 4:
 *
 *   normal   plain
 *   warn     triangle left of the label, header band in the warn fill
 *   alarm    octagon left of the label (blinks until acknowledged), tile filled
 *   stale    45 degree hatch, value "---"
 */
#ifndef UI_TILE_H
#define UI_TILE_H

#include "lvgl.h"
#include "hh_data.h"

typedef enum { UI_TILE_BIG, UI_TILE_SMALL } ui_tile_kind_t;
typedef struct ui_tile ui_tile_t;

/* ch < 0 makes an empty slot. The tile frees itself with its parent. */
ui_tile_t *ui_tile_create(lv_obj_t *parent, ui_tile_kind_t kind, int x, int y, int w, int h, int ch);
/* octagon_on: the blink phase for an unacknowledged alarm (true = shown). */
void ui_tile_update(ui_tile_t *t, const hh_snapshot_t *s, bool octagon_on);

#endif
