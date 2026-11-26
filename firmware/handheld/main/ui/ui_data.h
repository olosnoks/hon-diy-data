/* Helpers between the hh-core snapshot and the widgets. */
#ifndef UI_DATA_H
#define UI_DATA_H

#include <stdbool.h>
#include <stddef.h>
#include "lvgl.h"
#include "hh_data.h"

/* The channels in the display units (ui_units.h): units, lo/hi, limits and
 * decimals converted. Values from the snapshot go through ui_value first. */
const hh_channel_info_t *ui_channels(uint16_t *n);
/* A snapshot value of channel ch in its display unit (NaN stays NaN). Uses the
 * conversion of the last ui_channels() call. */
float ui_value(int ch, float v);
/* First channel whose name equals one of the '|'-separated candidates,
 * case-insensitive. -1 if none. */
int ui_find_channel(const char *candidates);
/* A 0/1 channel shown as ON / OFF: lo 0, hi 1, no decimals. */
bool ui_is_flag(const hh_channel_info_t *c);
void ui_fmt_value(char *buf, size_t n, const hh_channel_info_t *c, float v);
void ui_fmt_float(char *buf, size_t n, float v, int decimals);
/* "> 108" / "< 11.5": the limit the value is past, for the banner. */
void ui_fmt_limit(char *buf, size_t n, const hh_channel_info_t *c, float v, int level);
void ui_upper(char *dst, size_t n, const char *src);
/* lv_label_set_text only when the text differs: unchanged labels cost no redraw. */
void ui_set_text(lv_obj_t *label, const char *text);

/* Widget shorthands. Objects have no theme: transparent, no border, no padding. */
lv_obj_t *ui_box(lv_obj_t *parent, int x, int y, int w, int h);
lv_obj_t *ui_label(lv_obj_t *parent, const lv_font_t *font, int x, int y, int w, lv_text_align_t align);
void ui_fill(lv_obj_t *obj, lv_color_t c);
void ui_border(lv_obj_t *obj, lv_color_t c, int width, lv_border_side_t sides);
void ui_ink(lv_obj_t *label, lv_color_t c);

#endif
