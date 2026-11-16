/* Design tokens from docs/design/HANDOFF.md, ported to the 480x272 handheld.
 * Daylight (black on white) is the default; Night is white on black. Only the
 * tokens differ between themes: layout, shapes and weights are the same.
 */
#ifndef UI_THEME_H
#define UI_THEME_H

#include <stdbool.h>
#include <stdint.h>
#include "lvgl.h"

typedef struct {
    uint32_t bg, panel, sunk;
    uint32_t fg, fg2;            /* text */
    uint32_t rule, rule2;        /* 2 px borders; dividers and track outlines (non-text) */
    uint32_t accent, on_accent;
    uint32_t warn, on_warn;      /* fill and the text on it */
    uint32_t alarm, on_alarm;
    uint32_t ok, unver;          /* verified / unverified marker */
} ui_tokens_t;

extern const ui_tokens_t UI_DAYLIGHT, UI_NIGHT;

/* Every (text, ground) pair the UI draws, for the contrast test. */
typedef struct { const char *name; uint32_t fg, bg; } ui_pair_t;
int ui_theme_pairs(const ui_tokens_t *t, ui_pair_t *out, int max);

void ui_theme_set(bool night);
bool ui_theme_night(void);
const ui_tokens_t *ui_tokens(void);
#define TK(field) lv_color_hex(ui_tokens()->field)

/* Inter, tabular digits, 4 bpp (tools/gen_fonts.py). 16 px is the smallest
 * text on the device: caps 12 px = 2.4 mm tall, 12 arc minutes at 65 cm. */
LV_FONT_DECLARE(hh_font_16)   /* Bold: labels, units, hints */
LV_FONT_DECLARE(hh_font_20)   /* ExtraBold: titles, list values, banner */
LV_FONT_DECLARE(hh_font_28)   /* ExtraBold: state words, secondary values */
LV_FONT_DECLARE(hh_num_36)    /* digits + ON/OFF: sensor tiles (digits 26 px tall) */
LV_FONT_DECLARE(hh_num_52)    /* digits: gauge tiles (38 px tall) */
LV_FONT_DECLARE(hh_num_84)    /* digits: GPS speed (62 px tall) */

/* Geometry, px */
#define UI_W        480
#define UI_H        272
#define UI_TOP_H    30
#define UI_HINT_H   26
#define UI_BODY_Y   UI_TOP_H
#define UI_BODY_H   (UI_H - UI_TOP_H - UI_HINT_H)
#define UI_RULE     2

#endif
