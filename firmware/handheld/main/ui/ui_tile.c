#include <math.h>
#include <stdio.h>
#include <string.h>

#include "ui_data.h"
#include "ui_shapes.h"
#include "ui_theme.h"
#include "ui_tile.h"

enum { ST_INIT = -1, ST_NORMAL, ST_WARN, ST_ALARM, ST_STALE };

struct ui_tile {
    lv_obj_t *root, *head, *icon, *label, *units, *marker, *value, *flag, *bar;
    const hh_channel_info_t *info;
    ui_tile_kind_t kind;
    int ch, state, bar_px, bar_w, units_x;
    bool is_flag;
    lv_color_t bar_fill, bar_track, bar_edge;
};

#define PAD 5

/* 45 degree hatch for "no data": 4 px stripes of sunk on the panel ground. */
static void hatch_cb(lv_event_t *e)
{
    ui_tile_t *t = lv_event_get_user_data(e);
    if (t->state != ST_STALE) return;
    lv_layer_t *layer = lv_event_get_layer(e);
    lv_area_t a;
    lv_obj_get_coords(t->root, &a);
    a.x1 += UI_RULE; a.y1 += UI_RULE; a.x2 -= UI_RULE; a.y2 -= UI_RULE;
    lv_draw_line_dsc_t d;
    lv_draw_line_dsc_init(&d);
    d.color = TK(sunk); d.width = 5; d.opa = LV_OPA_COVER;
    for (int c = a.x1 + a.y1 + 6; c < a.x2 + a.y2; c += 14) {
        int xa = LV_MAX(a.x1, c - a.y2), xb = LV_MIN(a.x2, c - a.y1);
        d.p1.x = xa; d.p1.y = c - xa; d.p2.x = xb; d.p2.y = c - xb;
        lv_draw_line(layer, &d);
    }
}

/* Linear gauge: sunk track with a 2 px outline, filled from the channel
 * minimum to the value. */
static void bar_cb(lv_event_t *e)
{
    ui_tile_t *t = lv_event_get_user_data(e);
    lv_layer_t *layer = lv_event_get_layer(e);
    lv_area_t a;
    lv_obj_get_coords(t->bar, &a);
    lv_draw_rect_dsc_t d;
    lv_draw_rect_dsc_init(&d);
    d.bg_color = t->bar_track; d.bg_opa = LV_OPA_COVER;
    d.border_color = t->bar_edge; d.border_width = UI_RULE; d.border_opa = LV_OPA_COVER;
    lv_draw_rect(layer, &d, &a);
    if (t->bar_px > 0) {
        lv_area_t f = {a.x1, a.y1, a.x1 + t->bar_px - 1, a.y2};
        lv_draw_rect_dsc_init(&d);
        d.bg_color = t->bar_fill; d.bg_opa = LV_OPA_COVER;
        lv_draw_rect(layer, &d, &f);
    }
}

static void delete_cb(lv_event_t *e)
{
    lv_free(lv_event_get_user_data(e));
}

ui_tile_t *ui_tile_create(lv_obj_t *parent, ui_tile_kind_t kind, int x, int y, int w, int h, int ch)
{
    ui_tile_t *t = lv_malloc(sizeof(*t));
    LV_ASSERT_MALLOC(t);
    memset(t, 0, sizeof(*t));
    t->kind = kind;
    t->ch = ch;
    t->state = ST_INIT;

    t->root = ui_box(parent, x, y, w, h);
    ui_fill(t->root, TK(panel));
    ui_border(t->root, TK(rule), UI_RULE, LV_BORDER_SIDE_FULL);
    lv_obj_add_event_cb(t->root, delete_cb, LV_EVENT_DELETE, t);
    if (ch < 0) return t;

    uint16_t n;
    const hh_channel_info_t *info = ui_channels(&n);
    t->info = &info[ch];
    t->is_flag = ui_is_flag(t->info);
    lv_obj_add_event_cb(t->root, hatch_cb, LV_EVENT_DRAW_MAIN_END, t);
    lv_obj_set_style_border_post(t->root, true, 0);   /* the border goes over the hatch ends */

    bool big = kind == UI_TILE_BIG;
    const lv_font_t *value_font = big ? &hh_num_52 : &hh_num_36;
    int inner_w = w - 2 * UI_RULE, inner_h = h - 2 * UI_RULE;
    int head_h = big ? 22 : 20, mark = 18;

    /* header: [state shape] LABEL ........ units */
    t->head = ui_box(t->root, 0, 0, inner_w, head_h);
    t->icon = ui_shape_create(t->head, 14);
    lv_obj_set_pos(t->icon, PAD, (head_h - 14) / 2);
    lv_obj_add_flag(t->icon, LV_OBJ_FLAG_HIDDEN);

    char text[24];
    lv_point_t size;
    lv_text_get_size(&size, t->info->units, &hh_font_16, 0, 0, LV_COORD_MAX, LV_TEXT_FLAG_NONE);
    t->units_x = inner_w - PAD - (t->info->units[0] ? size.x : 0);
    t->units = ui_label(t->head, &hh_font_16, t->units_x, (head_h - 20) / 2, 0, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(t->units, t->info->units);

    ui_upper(text, sizeof(text), t->info->label[0] ? t->info->label : t->info->name);
    t->label = ui_label(t->head, &hh_font_16, PAD, (head_h - 20) / 2, t->units_x - 4 - PAD, LV_TEXT_ALIGN_LEFT);
    lv_label_set_text(t->label, text);

    /* value, right-aligned so the ones place does not move */
    int value_h = lv_font_get_line_height(value_font);
    int value_y = big ? head_h + 6 : inner_h - value_h - 2;
    t->value = ui_label(t->root, value_font, PAD, value_y, inner_w - 2 * PAD, LV_TEXT_ALIGN_RIGHT);
    lv_label_set_long_mode(t->value, LV_LABEL_LONG_CLIP);

    /* verified / unverified marker: beside the gauge on a big tile, at the
     * left of the value row on a small one (the value is right-aligned) */
    t->marker = ui_shape_create(t->root, mark);
    int bar_y = value_y + value_h + 10;
    if (big) lv_obj_set_pos(t->marker, inner_w - PAD - mark, bar_y - 2);
    else lv_obj_set_pos(t->marker, PAD, value_y + (value_h - mark) / 2);

    if (t->is_flag) {
        t->flag = ui_shape_create(t->root, value_h - 4);
        lv_obj_set_pos(t->flag, PAD + (big ? 0 : mark + 8), value_y + 2);
    } else if (big) {
        t->bar_w = inner_w - 2 * PAD - mark - 6;
        t->bar = ui_box(t->root, PAD, bar_y, t->bar_w, 12);
        lv_obj_add_event_cb(t->bar, bar_cb, LV_EVENT_DRAW_MAIN, t);
    }
    return t;
}

static void apply_state(ui_tile_t *t, int state)
{
    lv_color_t ground = TK(panel), ink = TK(fg), ink2 = TK(fg2), head = TK(panel), head_ink = TK(fg);
    lv_color_t mark = t->info->verified ? TK(ok) : TK(unver), mark_ink = TK(panel);
    bool head_fill = false;
    t->bar_fill = TK(fg); t->bar_track = TK(sunk); t->bar_edge = TK(rule2);

    if (state == ST_WARN) {
        head = TK(warn); head_ink = TK(on_warn); ink2 = TK(on_warn);
        head_fill = true;
    } else if (state == ST_ALARM) {
        ground = head = TK(alarm); ink = head_ink = ink2 = TK(on_alarm);
        mark = TK(on_alarm); mark_ink = TK(alarm);
        t->bar_fill = TK(on_alarm); t->bar_track = TK(alarm); t->bar_edge = TK(on_alarm);
    }
    ui_fill(t->root, ground);
    if (head_fill) ui_fill(t->head, head);
    else lv_obj_set_style_bg_opa(t->head, LV_OPA_TRANSP, 0);
    ui_ink(t->label, head_ink);
    ui_ink(t->units, state == ST_NORMAL || state == ST_STALE ? ink2 : head_ink);
    ui_ink(t->value, ink);
    ui_shape_set(t->marker, t->info->verified ? UI_SHAPE_VERIFIED : UI_SHAPE_UNVERIFIED, mark, mark_ink);

    /* the state shape sits left of the label and pushes it right */
    bool icon = state == ST_WARN || state == ST_ALARM;
    int icon_w = icon ? lv_obj_get_width(t->icon) + 4 : 0;
    ui_shape_set(t->icon, state == ST_ALARM ? UI_SHAPE_OCTAGON : UI_SHAPE_TRIANGLE, head_ink, head_ink);
    if (icon) lv_obj_remove_flag(t->icon, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(t->icon, LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_x(t->label, PAD + icon_w);
    lv_obj_set_width(t->label, t->units_x - 4 - PAD - icon_w);

    if (t->bar) lv_obj_invalidate(t->bar);
    lv_obj_invalidate(t->root);
}

void ui_tile_update(ui_tile_t *t, const hh_snapshot_t *s, bool octagon_on)
{
    if (t->ch < 0) return;
    bool have = t->ch < s->n_ch && s->fresh[t->ch];
    int state = !have ? ST_STALE : s->alarm[t->ch] >= 2 ? ST_ALARM : s->alarm[t->ch] == 1 ? ST_WARN : ST_NORMAL;
    if (state != t->state) {
        t->state = state;
        apply_state(t, state);
    }
    if (state == ST_ALARM) {
        if (octagon_on) lv_obj_remove_flag(t->icon, LV_OBJ_FLAG_HIDDEN);
        else lv_obj_add_flag(t->icon, LV_OBJ_FLAG_HIDDEN);
    }

    char text[16];
    float v = have ? s->value[t->ch] : NAN;
    if (have) ui_fmt_value(text, sizeof(text), t->info, v);
    else snprintf(text, sizeof(text), "---");
    ui_set_text(t->value, text);

    if (t->flag) {
        lv_color_t c = lv_obj_get_style_text_color(t->value, 0);
        if (!have) ui_shape_set(t->flag, UI_SHAPE_NONE, c, c);
        else ui_shape_set(t->flag, v >= 0.5f ? UI_SHAPE_SQUARE_FILLED : UI_SHAPE_SQUARE, c, c);
    }
    if (t->bar) {
        float span = t->info->hi - t->info->lo;
        float f = (have && span > 0) ? (v - t->info->lo) / span : 0;
        if (!(f > 0)) f = 0;
        if (f > 1) f = 1;
        int px = (int)(f * (float)t->bar_w + 0.5f);
        if (px != t->bar_px) {
            t->bar_px = px;
            lv_obj_invalidate(t->bar);
        }
    }
}
